import AppKit
import SwiftUI
import Darwin

// ── Version ───────────────────────────────────────────────────────────────────
// Version is fetched live from Flask /api/version so menu and web UI always match.
private let APP_VERSION_FALLBACK = "…"

// ── Single-instance lock ──────────────────────────────────────────────────────

private var lockDescriptor: Int32 = -1

/// Hold an OS lock for this data store, including across simultaneous launches.
private func acquireLock() -> Bool {
    let directory = runtimeDataRoot
    do {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    } catch { return false }
    let descriptor = Darwin.open(directory.appendingPathComponent(".instance.lock").path,
                                 O_CREAT | O_RDWR | O_NOFOLLOW | O_CLOEXEC, 0o600)
    guard descriptor >= 0 else { return false }
    guard flock(descriptor, LOCK_EX | LOCK_NB) == 0 else {
        Darwin.close(descriptor)
        return false
    }
    lockDescriptor = descriptor
    return true
}

private func releaseLock() {
    if lockDescriptor >= 0 {
        flock(lockDescriptor, LOCK_UN)
        Darwin.close(lockDescriptor)
        lockDescriptor = -1
    }
}

// ── Project root resolution ───────────────────────────────────────────────────
//
// Layout:   <project>/EnergyMonitorApp/EnergyMonitorApp   (bare binary)
//        or <project>/EnergyMonitorApp/EnergyMonitorApp.app/Contents/MacOS/EnergyMonitorApp
//        or /Applications/EnergyMonitorApp.app/Contents/MacOS/EnergyMonitorApp
//
// When installed in /Applications, the app reads the repo path from
// Contents/Resources/project_root.txt (written by build.sh).

private func resolveProjectRoot() -> URL {
    let binaryURL = URL(fileURLWithPath: CommandLine.arguments[0]).standardizedFileURL
    if binaryURL.pathComponents.contains("Contents") {
        if let embeddedRoot = Bundle.main.url(forResource: "project_root", withExtension: "txt"),
           let raw = try? String(contentsOf: embeddedRoot, encoding: .utf8) {
            let path = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            if !path.isEmpty {
                return URL(fileURLWithPath: path)
            }
        }
        return binaryURL
            .deletingLastPathComponent() // binary name
            .deletingLastPathComponent() // MacOS/
            .deletingLastPathComponent() // Contents/
            .deletingLastPathComponent() // EnergyMonitorApp.app/
            .deletingLastPathComponent() // EnergyMonitorApp/ (subdirectory)
    }
    return binaryURL
        .deletingLastPathComponent() // EnergyMonitorApp/
        .deletingLastPathComponent() // project root
}

private let projectRoot   = resolveProjectRoot()
private let runtimeDataRoot: URL = {
    if let embedded = Bundle.main.url(forResource: "data_root", withExtension: "txt"),
       let raw = try? String(contentsOf: embedded, encoding: .utf8) {
        let path = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if !path.isEmpty { return URL(fileURLWithPath: path) }
    }
    return projectRoot
}()
private let venvPython    = projectRoot.appendingPathComponent("venv/bin/python3").path
private let flaskPort: String = {
    let embedded = Bundle.main.url(forResource: "flask_port", withExtension: "txt")
        .flatMap { try? String(contentsOf: $0, encoding: .utf8) }?
        .trimmingCharacters(in: .whitespacesAndNewlines)
    return ProcessInfo.processInfo.environment["FLASK_PORT"] ?? embedded ?? "5051"
}()
private let collectorURLSetting = ProcessInfo.processInfo.environment["ENERGY_COLLECTOR_URL"]
    ?? UserDefaults.standard.string(forKey: "collectorURL")
private let isCollectorClient = collectorURLSetting != nil
private let dashboardURL = URL(string: collectorURLSetting ?? "http://127.0.0.1:\(flaskPort)")
    ?? URL(string: "http://127.0.0.1:\(flaskPort)")!

private func validateCollectorURL(_ setting: String? = collectorURLSetting) -> String? {
    guard let raw = setting else { return nil }
    guard let url = URL(string: raw),
          let host = url.host, !host.isEmpty,
          ["http", "https"].contains(url.scheme ?? ""),
          url.user == nil, url.password == nil,
          url.query == nil, url.fragment == nil,
          url.path.isEmpty || url.path == "/" else {
        return "ENERGY_COLLECTOR_URL must be an HTTP(S) server origin without credentials or a path."
    }
    if url.scheme == "http" && !["localhost", "127.0.0.1", "::1"].contains(host) {
        return "Use HTTPS or a loopback SSH tunnel for the collector connection."
    }
    return nil
}

private func validateProjectRoot() -> String? {
    let fm = FileManager.default
    let requiredPaths = [
        projectRoot.appendingPathComponent("web.py").path,
        projectRoot.appendingPathComponent("energy.py").path,
        projectRoot.appendingPathComponent("venv/bin/python3").path,
    ]
    for path in requiredPaths where !fm.fileExists(atPath: path) {
        return "Missing required file: \(path)"
    }

    let certCheck = Process()
    let out = Pipe()
    certCheck.executableURL = URL(fileURLWithPath: venvPython)
    certCheck.arguments = [
        "-c",
        "import certifi, os, sys; p = certifi.where(); sys.stdout.write(p if os.path.exists(p) else '')",
    ]
    certCheck.currentDirectoryURL = projectRoot
    certCheck.standardOutput = out
    certCheck.standardError = Pipe()

    do {
        try certCheck.run()
        certCheck.waitUntilExit()
        let certPath = String(
            data: out.fileHandleForReading.readDataToEndOfFile(),
            encoding: .utf8
        )?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if certCheck.terminationStatus != 0 || certPath.isEmpty {
            return "The bundled project environment is invalid for \(projectRoot.path). Rebuild or launch the app from the current repository."
        }
    } catch {
        return "Could not validate the project environment at \(projectRoot.path): \(error.localizedDescription)"
    }

    return nil
}

// ── AppDelegate ───────────────────────────────────────────────────────────────

class AppDelegate: NSObject, NSApplicationDelegate, NSPopoverDelegate {
    var flaskProcess: Process?
    private var pollerProcess: Process?
    var statusItem: NSStatusItem?
    private var ownsLock = false
    private var monitorTimer: Timer?
    private let monitor = MenuMonitor(baseURL: dashboardURL)
    private var popover: NSPopover?
    private var contextMenu: NSMenu?
    private weak var healthMenuItem: NSMenuItem?

    private let probeInterval: TimeInterval = 0.5
    private let probeTimeout:  TimeInterval = 20.0

    // Keep direct references so titles can be updated without fragile index arithmetic.
    private weak var copyURLMenuItem: NSMenuItem?
    private weak var headerMenuItem: NSMenuItem?
    private weak var autostartMenuItem: NSMenuItem?

    func applicationDidFinishLaunching(_ notification: Notification) {
        guard acquireLock() else {
            let alert = NSAlert()
            alert.messageText = "Energy Monitor is already running"
            alert.informativeText = "Look for the ⚡ icon in the menu bar."
            alert.alertStyle = .informational
            alert.addButton(withTitle: "OK")
            alert.runModal()
            NSApp.terminate(nil)
            return
        }

        ownsLock = true
        print("Energy Monitor — project root: \(projectRoot.path)")
        if let problem = validateCollectorURL() ?? (isCollectorClient ? nil : validateProjectRoot()) {
            let alert = NSAlert()
            alert.messageText = "Invalid project environment"
            alert.informativeText = problem
            alert.alertStyle = .critical
            alert.addButton(withTitle: "OK")
            alert.runModal()
            if ownsLock { releaseLock() }
            NSApp.terminate(nil)
            return
        }
        NSApp.setActivationPolicy(.accessory)
        enableAutostartOnFirstRun()
        monitor.autostart = LoginItem.isEnabled
        if !isCollectorClient {
            startFlaskServer()
            startPollerIfNeeded()
        }
        buildStatusItem()
        waitForFlask()
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag { togglePopover(nil) }
        return false
    }

    // ── Menu-bar status item ──────────────────────────────────────────────────

    private func buildStatusItem() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        if let btn = item.button {
            btn.image = NSImage(systemSymbolName: "bolt.fill",
                                accessibilityDescription: "Energy Monitor")
            btn.image?.isTemplate = true
            btn.target = self
            btn.action = #selector(togglePopover(_:))
            btn.sendAction(on: [.leftMouseUp, .rightMouseUp])
            btn.setAccessibilityLabel("Energy Monitor")
        }

        let menu = NSMenu()

        // ── Header: app name + version (fetched live from Flask) ──
        let headerItem = NSMenuItem(title: "Energy Monitor  v\(APP_VERSION_FALLBACK)",
                                    action: nil,
                                    keyEquivalent: "")
        headerItem.isEnabled = false
        menu.addItem(headerItem)
        headerMenuItem = headerItem

        let healthItem = NSMenuItem(title: "Connecting to monitor…", action: nil, keyEquivalent: "")
        healthItem.isEnabled = false
        menu.addItem(healthItem)
        healthMenuItem = healthItem
        menu.addItem(.separator())

        // ── Primary actions ──
        let openItem = NSMenuItem(title: "Open Dashboard",
                                  action: #selector(openInBrowser),
                                  keyEquivalent: "")
        openItem.target = self
        menu.addItem(openItem)

        let copyItem = NSMenuItem(title: "Copy Dashboard URL",
                                  action: #selector(copyLocalURL),
                                  keyEquivalent: "")
        copyItem.target = self
        menu.addItem(copyItem)
        copyURLMenuItem = copyItem

        let connectionItem = NSMenuItem(title: "Collector Connection…",
                                        action: #selector(configureCollector),
                                        keyEquivalent: "")
        connectionItem.target = self
        menu.addItem(connectionItem)


        let autostartItem = NSMenuItem(title: "Start at Login",
                                       action: #selector(toggleAutostart),
                                       keyEquivalent: "")
        autostartItem.target = self
        autostartItem.state = LoginItem.isEnabled ? .on : .off
        menu.addItem(autostartItem)
        autostartMenuItem = autostartItem

        menu.addItem(.separator())

        // ── Uninstall ──
        let uninstallItem = NSMenuItem(title: "Uninstall Energy Monitor…",
                                       action: #selector(showUninstall),
                                       keyEquivalent: "")
        uninstallItem.target = self
        menu.addItem(uninstallItem)

        menu.addItem(.separator())

        // ── Quit ──
        menu.addItem(NSMenuItem(title: "Quit",
                                action: #selector(NSApplication.terminate(_:)),
                                keyEquivalent: "q"))

        contextMenu = menu
        statusItem = item
        let panel = NSPopover()
        panel.behavior = .transient
        panel.delegate = self
        panel.contentSize = NSSize(width: 440, height: 560)
        panel.contentViewController = NSHostingController(rootView: MonitorPopover(
            monitor: monitor,
            openDashboard: { [weak self] in self?.openInBrowser() },
            openSettings: { [weak self] in self?.openSettings() },
            toggleAutostart: { [weak self] in self?.toggleAutostart() },
            uninstall: { [weak self] in self?.showUninstall() },
            quit: { NSApp.terminate(nil) }
        ))
        popover = panel
        monitor.didUpdate = { [weak self] in
            guard let self = self else { return }
            let power = self.monitor.online ? self.monitor.summary?.currentWatts.map { String(format: "%.0f W", $0) } ?? "No readings" : "Offline"
            self.statusItem?.button?.toolTip = "Energy Monitor — " + power
            self.statusItem?.button?.setAccessibilityValue(power)
            self.healthMenuItem?.title = self.monitor.online ? "Live · minute-average power" : "Poller offline or stale — check Settings"
            if let version = self.monitor.summary?.version {
                self.headerMenuItem?.title = "Energy Monitor  v\(version)"
            }
        }
        let timer = Timer(timeInterval: 15, repeats: true) { [weak self] _ in self?.refreshMonitor() }
        monitorTimer = timer
        RunLoop.main.add(timer, forMode: .common)
        refreshMonitor()
    }

    /// Start at login is on by default; the marker keeps a later "off" choice from being undone.
    private func enableAutostartOnFirstRun() {
        let marker = runtimeDataRoot.appendingPathComponent(".autostart_configured")
        guard !FileManager.default.fileExists(atPath: marker.path) else { return }
        try? LoginItem.enable()
        FileManager.default.createFile(atPath: marker.path, contents: nil)
    }

    @objc private func toggleAutostart() {
        if LoginItem.isEnabled { LoginItem.disable() } else { try? LoginItem.enable() }
        monitor.autostart = LoginItem.isEnabled
        autostartMenuItem?.state = monitor.autostart ? .on : .off
    }

    @objc private func openInBrowser() {
        popover?.performClose(nil)
        NSWorkspace.shared.open(dashboardURL)
    }

    @objc private func togglePopover(_ sender: Any?) {
        guard let button = statusItem?.button else { return }
        if NSApp.currentEvent?.type == .rightMouseUp, let menu = contextMenu {
            popover?.performClose(nil)
            menu.popUp(positioning: nil, at: NSPoint(x: 0, y: button.bounds.maxY), in: button)
            return
        }
        if popover?.isShown == true {
            popover?.performClose(nil)
        } else {
            NSApp.activate(ignoringOtherApps: true)
            monitor.refresh()
            popover?.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
        }
    }

    func popoverDidClose(_ notification: Notification) { monitor.goBack() }

    private func openSettings() {
        popover?.performClose(nil)
        NSWorkspace.shared.open(dashboardURL.appendingPathComponent("settings"))
    }

    @objc private func configureCollector() {
        let alert = NSAlert()
        alert.messageText = "Collector Connection"
        alert.informativeText = "Enter the collector origin, or leave blank for local mode. Changes apply on the next launch. An ENERGY_COLLECTOR_URL environment variable overrides this setting. This does not stop a separately running poller."
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 360, height: 24))
        field.stringValue = UserDefaults.standard.string(forKey: "collectorURL") ?? ""
        field.placeholderString = "http://127.0.0.1:15001"
        let cacheField = NSTextField(frame: NSRect(x: 0, y: 0, width: 360, height: 24))
        let paths = UserDefaults.standard.dictionary(forKey: "collectorHistoryCachePaths") as? [String: String]
        cacheField.stringValue = paths?[dashboardURL.absoluteString] ?? ""
        cacheField.placeholderString = "Optional downloaded history cache: /path/collector-cache.db"
        let inputs = NSStackView(views: [field, cacheField])
        inputs.orientation = .vertical
        inputs.alignment = .leading
        inputs.spacing = 8
        inputs.frame = NSRect(x: 0, y: 0, width: 360, height: 56)
        alert.accessoryView = inputs
        alert.addButton(withTitle: "Save")
        alert.addButton(withTitle: "Cancel")
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        let value = field.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        let cachePath = NSString(string: cacheField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines))
            .expandingTildeInPath
        if !cachePath.isEmpty && !cachePath.hasPrefix("/") {
            let error = NSAlert()
            error.messageText = "Invalid cache path"
            error.informativeText = "Use an absolute path to the downloaded cache database."
            error.runModal()
            return
        }
        if let problem = validateCollectorURL(value.isEmpty ? nil : value) {
            let error = NSAlert()
            error.messageText = "Invalid collector address"
            error.informativeText = problem
            error.runModal()
            return
        }
        if value.isEmpty {
            UserDefaults.standard.removeObject(forKey: "collectorURL")
        } else {
            UserDefaults.standard.set(value, forKey: "collectorURL")
        }
        let origin = value.isEmpty ? "http://127.0.0.1:\(flaskPort)" : URL(string: value)!.absoluteString
        var savedPaths = paths ?? [:]
        if cachePath.isEmpty {
            savedPaths.removeValue(forKey: origin)
        } else if cachePath.hasPrefix("/") {
            savedPaths[origin] = cachePath
        }
        UserDefaults.standard.set(savedPaths, forKey: "collectorHistoryCachePaths")
    }

    @objc private func copyLocalURL() {
        let url = dashboardURL.absoluteString
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(url, forType: .string)
        copyURLMenuItem?.title = "Copied!"
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in
            self?.copyURLMenuItem?.title = "Copy Dashboard URL"
        }
    }

    // ── Uninstall ─────────────────────────────────────────────────────────────

    @objc private func showUninstall() {
        popover?.performClose(nil)
        let alert = NSAlert()
        alert.messageText = "Uninstall Energy Monitor?"
        alert.informativeText = LoginItem.isBrewInstall
            ? "Start at login is turned off and Homebrew removes the app. Your energy history and settings are kept unless you choose to delete them."
            : "Start at login is turned off and the app is moved to the Trash. Your project files and database are not affected."
        alert.alertStyle = .warning
        alert.addButton(withTitle: "Uninstall")
        alert.addButton(withTitle: "Cancel")
        if LoginItem.isBrewInstall {
            alert.showsSuppressionButton = true
            alert.suppressionButton?.title = "Also delete my energy history and settings"
        }
        guard alert.runModal() == .alertFirstButtonReturn else { return }

        let purge = alert.suppressionButton?.state == .on
        pollerProcess?.terminate()
        flaskProcess?.terminate()
        if ownsLock { releaseLock() }
        let message = Uninstaller.run(dataRoot: runtimeDataRoot, purge: purge)
        let done = NSAlert()
        done.messageText = "Energy Monitor is being removed"
        done.informativeText = message
        done.addButton(withTitle: "OK")
        done.runModal()
        NSApp.terminate(nil)
    }

    private func refreshMonitor() { monitor.refresh() }

    // ── Live version fetch ────────────────────────────────────────────────

    private func fetchVersionFromFlask() {
        let url = dashboardURL.appendingPathComponent("api/version")
        URLSession.shared.dataTask(with: url) { [weak self] data, _, _ in
            guard let data = data,
                  let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let version = json["version"] as? String else { return }
            DispatchQueue.main.async {
                self?.headerMenuItem?.title = "Energy Monitor  v\(version)"
            }
        }.resume()
    }

    // ── Lifecycle ─────────────────────────────────────────────────────────────

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        return false
    }

    func applicationWillTerminate(_ notification: Notification) {
        monitorTimer?.invalidate()
        pollerProcess?.terminate()
        flaskProcess?.terminate()
        if ownsLock { releaseLock() }
    }

    // ── Orphan cleanup ────────────────────────────────────────────────────────

    /// Kill any process on the configured Flask port that is provably our own Flask
    /// (web.py from this project's venv). Unrelated Flask apps are left alone.
    private func killOrphanedFlask() {
        // Step 1: get PIDs listening on the configured port
        let lsof = Process()
        let lsofOut = Pipe()
        lsof.executableURL = URL(fileURLWithPath: "/usr/sbin/lsof")
        lsof.arguments = ["-i", ":\(flaskPort)", "-t"]
        lsof.standardOutput = lsofOut
        lsof.standardError  = Pipe()
        guard (try? lsof.run()) != nil else { return }
        lsof.waitUntilExit()

        let raw = String(data: lsofOut.fileHandleForReading.readDataToEndOfFile(),
                         encoding: .utf8) ?? ""
        let pids = raw.components(separatedBy: .newlines)
                      .compactMap { Int32($0.trimmingCharacters(in: .whitespaces)) }


        for pid in pids {
            // Step 2: confirm the process is ours by checking its command line
            let ps = Process()
            let psOut = Pipe()
            ps.executableURL = URL(fileURLWithPath: "/bin/ps")
            ps.arguments = ["-p", String(pid), "-o", "command="]
            ps.standardOutput = psOut
            ps.standardError  = Pipe()
            guard (try? ps.run()) != nil else { continue }
            ps.waitUntilExit()

            let cmd = String(data: psOut.fileHandleForReading.readDataToEndOfFile(),
                             encoding: .utf8) ?? ""

            // Only kill if the command uses our project's venv python AND web.py
            if cmd.contains("web.py") && processBelongsToProject(String(pid)) {
                kill(pid, SIGTERM)
                print("Killed orphaned Flask process (PID \(pid))")
            }
        }

        // Brief pause to let the port free up
        Thread.sleep(forTimeInterval: 0.5)
    }

    private func processBelongsToProject(_ pid: String) -> Bool {
        let process = Process()
        let output = Pipe()
        process.executableURL = URL(fileURLWithPath: "/usr/sbin/lsof")
        process.arguments = ["-a", "-p", pid, "-d", "cwd", "-Fn"]
        process.standardOutput = output
        process.standardError = FileHandle.nullDevice
        guard (try? process.run()) != nil else { return false }
        let text = String(data: output.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
        process.waitUntilExit()
        let directories = Set([
            projectRoot.path,
            projectRoot.resolvingSymlinksInPath().path,
            runtimeDataRoot.path,
            runtimeDataRoot.resolvingSymlinksInPath().path,
        ])
        return text.components(separatedBy: .newlines).contains { line in
            guard line.hasPrefix("n") else { return false }
            return directories.contains(String(line.dropFirst()))
        }
    }

    // The menu app owns polling when no independently launched poller is running.
    private func startPollerIfNeeded() {
        let check = Process()
        let output = Pipe()
        check.executableURL = URL(fileURLWithPath: "/bin/ps")
        check.arguments = ["-axo", "pid=,command="]
        check.standardOutput = output
        do {
            try check.run()
            let commands = String(data: output.fileHandleForReading.readDataToEndOfFile(),
                                  encoding: .utf8) ?? ""
            check.waitUntilExit()
            if commands.components(separatedBy: .newlines).contains(where: {
                let fields = $0.split(maxSplits: 1, whereSeparator: { $0.isWhitespace })
                guard fields.count == 2, fields[1].hasSuffix(" energy.py") || fields[1].hasSuffix("/energy.py") else { return false }
                return processBelongsToProject(String(fields[0]))
            }) { return }
            try FileManager.default.createDirectory(at: runtimeDataRoot, withIntermediateDirectories: true)
            let logURL = runtimeDataRoot.appendingPathComponent("poller.log")
            if !FileManager.default.fileExists(atPath: logURL.path) {
                FileManager.default.createFile(atPath: logURL.path, contents: nil)
            }
            let log = try FileHandle(forWritingTo: logURL)
            defer { try? log.close() }
            try log.seekToEnd()
            let process = Process()
            process.executableURL = URL(fileURLWithPath: venvPython)
            process.arguments = ["-u", projectRoot.appendingPathComponent("energy.py").path]
            process.currentDirectoryURL = runtimeDataRoot
            process.standardOutput = log
            process.standardError = log
            try process.run()
            pollerProcess = process
        } catch {
            print("Failed to start poller: \(error.localizedDescription)")
        }
    }

    // ── Flask startup ─────────────────────────────────────────────────────────

    private func startFlaskServer() {
        killOrphanedFlask()

        let process = Process()
        let pipe    = Pipe()

        process.executableURL       = URL(fileURLWithPath: venvPython)
        process.arguments           = [projectRoot.appendingPathComponent("web.py").path]
        do {
            try FileManager.default.createDirectory(at: runtimeDataRoot, withIntermediateDirectories: true)
        } catch {
            print("Could not create runtime data directory: \(error.localizedDescription)")
            return
        }
        process.currentDirectoryURL = runtimeDataRoot
        process.standardOutput      = pipe
        process.standardError       = pipe

        var env = ProcessInfo.processInfo.environment
        env["FLASK_PORT"] = flaskPort
        env["FLASK_ENV"]   = "production"
        env["VIRTUAL_ENV"] = projectRoot.appendingPathComponent("venv").path
        env["PATH"]        = projectRoot.appendingPathComponent("venv/bin").path
                           + ":/usr/local/bin:/usr/bin:/bin"
        process.environment = env

        do {
            try process.run()
            flaskProcess = process
            print("Flask process started (PID \(process.processIdentifier))")
        } catch {
            print("Failed to start Flask: \(error)")
        }

        DispatchQueue.global(qos: .background).async {
            let handle = pipe.fileHandleForReading
            NotificationCenter.default.addObserver(
                forName: .NSFileHandleDataAvailable,
                object: handle, queue: nil) { _ in
                let data = handle.availableData
                if !data.isEmpty, let text = String(data: data, encoding: .utf8) {
                    print("[Flask] \(text)", terminator: "")
                }
                handle.waitForDataInBackgroundAndNotify()
            }
            handle.waitForDataInBackgroundAndNotify()
        }
    }

    private func reportPortConflict() {
        monitor.summaryError = "Port \(flaskPort) is used by another app. Set FLASK_PORT to a free port and relaunch."
        let alert = NSAlert()
        alert.messageText = "Port \(flaskPort) is in use"
        alert.informativeText = "Another service answers on 127.0.0.1:\(flaskPort), so the dashboard cannot start. Rebuild with FLASK_PORT set to a free port, for example: FLASK_PORT=5052 ./build.sh --no-pull"
        alert.alertStyle = .warning
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }

    private func waitForFlask() {
        let deadline = Date().addingTimeInterval(probeTimeout)

        func probe() {
            let request = URLRequest(url: dashboardURL.appendingPathComponent("api/version"), timeoutInterval: 2)

            URLSession.shared.dataTask(with: request) { data, response, _ in
                let answered = response is HTTPURLResponse
                let json = data.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
                let ready = (response as? HTTPURLResponse)?.statusCode == 200 && json?["version"] is String
                DispatchQueue.main.async {
                    if ready {
                        print("Flask is ready — menu monitor active")
                        self.fetchVersionFromFlask()
                        self.refreshMonitor()
                    } else if answered && Date() >= deadline {
                        self.reportPortConflict()
                    } else if Date() < deadline {
                        DispatchQueue.main.asyncAfter(deadline: .now() + self.probeInterval) {
                            probe()
                        }
                    } else {
                        print("Flask did not respond in time — menu monitor will retry")
                        self.refreshMonitor()
                    }
                }
            }.resume()
        }

        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { probe() }
    }
}

// ── Entry point ───────────────────────────────────────────────────────────────

private func handleCommandLine(_ args: [String]) -> Int32? {
    if let index = args.firstIndex(of: "--autostart") {
        let action = index + 1 < args.count ? args[index + 1] : "status"
        switch action {
        case "on":
            do { try LoginItem.enable() } catch { print("Could not enable: \(error.localizedDescription)"); return 1 }
            print("Start at login: on")
        case "off":
            LoginItem.disable()
            print("Start at login: off")
        default:
            print("Start at login: \(LoginItem.isEnabled ? "on" : "off")")
        }
        return 0
    }
    if args.contains("--uninstall") {
        print(Uninstaller.run(dataRoot: runtimeDataRoot, purge: args.contains("--purge")))
        return 0
    }
    return nil
}

if let code = handleCommandLine(CommandLine.arguments) { exit(code) }

let app      = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
_ = NSApplicationMain(CommandLine.argc, CommandLine.unsafeArgv)
