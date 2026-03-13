import AppKit
import Darwin

// ── Version ───────────────────────────────────────────────────────────────────
// Version is fetched live from Flask /api/version so menu and web UI always match.
private let APP_VERSION_FALLBACK = "…"

// ── Single-instance lock ──────────────────────────────────────────────────────

private let kLockFile = "/tmp/com.dolbec.energymonitor.lock"

/// Returns true if we are the only running instance and the lock was acquired.
private func acquireLock() -> Bool {
    if let existing = try? String(contentsOfFile: kLockFile, encoding: .utf8)
                               .trimmingCharacters(in: .whitespacesAndNewlines),
       let pid = pid_t(existing),
       kill(pid, 0) == 0 {
        return false   // another instance is alive
    }
    let myPID = String(ProcessInfo.processInfo.processIdentifier)
    try? myPID.write(toFile: kLockFile, atomically: true, encoding: .utf8)
    return true
}

private func releaseLock() {
    try? FileManager.default.removeItem(atPath: kLockFile)
}

// ── Project root resolution ───────────────────────────────────────────────────
//
// Layout:   <project>/EnergyMonitorApp/EnergyMonitorApp   (bare binary)
//        or <project>/EnergyMonitorApp/EnergyMonitorApp.app/Contents/MacOS/EnergyMonitorApp

private func resolveProjectRoot() -> URL {
    let binaryURL = URL(fileURLWithPath: CommandLine.arguments[0]).standardizedFileURL
    if binaryURL.pathComponents.contains("Contents") {
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
private let venvPython    = projectRoot.appendingPathComponent("venv/bin/python3").path
private let dashboardURL  = URL(string: "http://localhost:5001")!

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

// ── Local IP helper ───────────────────────────────────────────────────────────

private func localNetworkIP() -> String? {
    var ifaddr: UnsafeMutablePointer<ifaddrs>?
    guard getifaddrs(&ifaddr) == 0 else { return nil }
    defer { freeifaddrs(ifaddr) }
    var ptr = ifaddr
    while let current = ptr {
        let ifa = current.pointee
        guard let addr = ifa.ifa_addr, addr.pointee.sa_family == UInt8(AF_INET) else {
            ptr = current.pointee.ifa_next
            continue
        }
        let name = String(cString: ifa.ifa_name)
        if name.hasPrefix("en") {
            var ipv4 = UnsafeRawPointer(addr)
                .assumingMemoryBound(to: sockaddr_in.self)
                .pointee
            var buf = [CChar](repeating: 0, count: Int(INET_ADDRSTRLEN))
            let result = withUnsafePointer(to: &ipv4.sin_addr) {
                inet_ntop(AF_INET, $0, &buf, socklen_t(INET_ADDRSTRLEN))
            }
            if result != nil {
                let ip = String(cString: buf)
                if ip != "0.0.0.0" { return ip }
            }
        }
        ptr = current.pointee.ifa_next
    }
    return nil
}

// ── AppDelegate ───────────────────────────────────────────────────────────────

class AppDelegate: NSObject, NSApplicationDelegate {
    var flaskProcess: Process?
    var statusItem: NSStatusItem?

    private let probeInterval: TimeInterval = 0.5
    private let probeTimeout:  TimeInterval = 20.0

    // Keep direct references so titles can be updated without fragile index arithmetic.
    private weak var copyURLMenuItem: NSMenuItem?
    private weak var headerMenuItem: NSMenuItem?

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

        print("Energy Monitor — project root: \(projectRoot.path)")
        if let problem = validateProjectRoot() {
            let alert = NSAlert()
            alert.messageText = "Invalid project environment"
            alert.informativeText = problem
            alert.alertStyle = .critical
            alert.addButton(withTitle: "OK")
            alert.runModal()
            releaseLock()
            NSApp.terminate(nil)
            return
        }
        NSApp.setActivationPolicy(.accessory)
        startFlaskServer()
        buildStatusItem()
        waitForFlaskThenOpen()
    }

    // ── Menu-bar status item ──────────────────────────────────────────────────

    private func buildStatusItem() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        if let btn = item.button {
            btn.image = NSImage(systemSymbolName: "bolt.fill",
                                accessibilityDescription: "Energy Monitor")
            btn.image?.isTemplate = true
        }

        let menu = NSMenu()

        // ── Header: app name + version (fetched live from Flask) ──
        let headerItem = NSMenuItem(title: "Energy Monitor  v\(APP_VERSION_FALLBACK)",
                                    action: nil,
                                    keyEquivalent: "")
        headerItem.isEnabled = false
        menu.addItem(headerItem)
        headerMenuItem = headerItem

        menu.addItem(.separator())

        // ── Primary actions ──
        let openItem = NSMenuItem(title: "Open Dashboard",
                                  action: #selector(openInBrowser),
                                  keyEquivalent: "")
        openItem.target = self
        menu.addItem(openItem)

        let copyItem = NSMenuItem(title: "Copy Local URL",
                                  action: #selector(copyLocalURL),
                                  keyEquivalent: "")
        copyItem.target = self
        menu.addItem(copyItem)
        copyURLMenuItem = copyItem

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

        item.menu = menu
        statusItem = item
    }

    @objc private func openInBrowser() {
        NSWorkspace.shared.open(dashboardURL)
    }

    @objc private func copyLocalURL() {
        let ip  = localNetworkIP() ?? "localhost"
        let url = "http://\(ip):5001"
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(url, forType: .string)
        copyURLMenuItem?.title = "Copied!"
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in
            self?.copyURLMenuItem?.title = "Copy Local URL"
        }
    }

    // ── Uninstall ─────────────────────────────────────────────────────────────

    @objc private func showUninstall() {
        let alert = NSAlert()
        alert.messageText = "Uninstall Energy Monitor?"
        alert.informativeText = "The app will be moved to the Trash. Your energy database and project files will not be affected."
        alert.alertStyle = .warning
        alert.addButton(withTitle: "Move to Trash")
        alert.addButton(withTitle: "Cancel")

        guard alert.runModal() == .alertFirstButtonReturn else { return }

        // Identify what to trash: walk up from the binary to find a .app bundle,
        // otherwise fall back to the bare binary itself.
        let binary = URL(fileURLWithPath: CommandLine.arguments[0]).standardizedFileURL
        var trashTarget = binary
        var candidate = binary
        while candidate.path != "/" {
            if candidate.pathExtension == "app" {
                trashTarget = candidate
                break
            }
            candidate = candidate.deletingLastPathComponent()
        }

        flaskProcess?.terminate()
        releaseLock()

        var resultURL: NSURL?
        do {
            try FileManager.default.trashItem(at: trashTarget, resultingItemURL: &resultURL)
            print("Moved to Trash: \(trashTarget.path)")
        } catch {
            let errAlert = NSAlert()
            errAlert.messageText = "Could not move to Trash"
            errAlert.informativeText = error.localizedDescription
            errAlert.alertStyle = .critical
            errAlert.addButton(withTitle: "OK")
            errAlert.runModal()
            return
        }

        NSApp.terminate(nil)
    }

    // ── Live version fetch ────────────────────────────────────────────────

    private func fetchVersionFromFlask() {
        guard let url = URL(string: "http://localhost:5001/api/version") else { return }
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
        flaskProcess?.terminate()
        releaseLock()
    }

    // ── Orphan cleanup ────────────────────────────────────────────────────────

    /// Kill any process on port 5001 that is provably our own Flask (web.py from
    /// this project's venv). Unrelated Flask apps on port 5001 are left alone.
    private func killOrphanedFlask() {
        // Step 1: get PIDs listening on :5001
        let lsof = Process()
        let lsofOut = Pipe()
        lsof.executableURL = URL(fileURLWithPath: "/usr/sbin/lsof")
        lsof.arguments = ["-i", ":5001", "-t"]
        lsof.standardOutput = lsofOut
        lsof.standardError  = Pipe()
        guard (try? lsof.run()) != nil else { return }
        lsof.waitUntilExit()

        let raw = String(data: lsofOut.fileHandleForReading.readDataToEndOfFile(),
                         encoding: .utf8) ?? ""
        let pids = raw.components(separatedBy: .newlines)
                      .compactMap { Int32($0.trimmingCharacters(in: .whitespaces)) }

        let venvPrefix = projectRoot.appendingPathComponent("venv").path

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
            if cmd.contains(venvPrefix) && cmd.contains("web.py") {
                kill(pid, SIGTERM)
                print("Killed orphaned Flask process (PID \(pid))")
            }
        }

        // Brief pause to let the port free up
        Thread.sleep(forTimeInterval: 0.5)
    }

    // ── Flask startup ─────────────────────────────────────────────────────────

    private func startFlaskServer() {
        killOrphanedFlask()

        let process = Process()
        let pipe    = Pipe()

        process.executableURL       = URL(fileURLWithPath: venvPython)
        process.arguments           = ["web.py"]
        process.currentDirectoryURL = projectRoot
        process.standardOutput      = pipe
        process.standardError       = pipe

        var env = ProcessInfo.processInfo.environment
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

    private func waitForFlaskThenOpen() {
        let deadline = Date().addingTimeInterval(probeTimeout)

        func probe() {
            var request = URLRequest(url: dashboardURL, timeoutInterval: probeInterval)
            request.httpMethod = "HEAD"

            URLSession.shared.dataTask(with: request) { _, response, _ in
                let ready = (response as? HTTPURLResponse)?.statusCode != nil
                DispatchQueue.main.async {
                    if ready {
                        print("Flask is ready — opening browser")
                        NSWorkspace.shared.open(dashboardURL)
                        self.fetchVersionFromFlask()
                    } else if Date() < deadline {
                        DispatchQueue.main.asyncAfter(deadline: .now() + self.probeInterval) {
                            probe()
                        }
                    } else {
                        print("Flask did not respond in time — opening browser anyway")
                        NSWorkspace.shared.open(dashboardURL)
                    }
                }
            }.resume()
        }

        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { probe() }
    }
}

// ── Entry point ───────────────────────────────────────────────────────────────

let app      = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
_ = NSApplicationMain(CommandLine.argc, CommandLine.unsafeArgv)
