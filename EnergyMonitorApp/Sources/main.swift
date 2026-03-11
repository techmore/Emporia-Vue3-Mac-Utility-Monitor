import AppKit
import Darwin

// ── Project root resolution ───────────────────────────────────────────────────
//
// Layout:   <project>/EnergyMonitorApp/EnergyMonitorApp   (binary)
//        or <project>/EnergyMonitorApp/EnergyMonitorApp.app/Contents/MacOS/EnergyMonitorApp
//
private func resolveProjectRoot() -> URL {
    let binaryURL = URL(fileURLWithPath: CommandLine.arguments[0]).standardizedFileURL
    if binaryURL.pathComponents.contains("Contents") {
        return binaryURL
            .deletingLastPathComponent() // MacOS/
            .deletingLastPathComponent() // Contents/
            .deletingLastPathComponent() // EnergyMonitorApp.app/
            .deletingLastPathComponent() // EnergyMonitorApp/
    }
    return binaryURL
        .deletingLastPathComponent() // EnergyMonitorApp/
        .deletingLastPathComponent() // project root
}

private let projectRoot = resolveProjectRoot()
private let venvPython  = projectRoot.appendingPathComponent("venv/bin/python3").path
private let dashboardURL = URL(string: "http://localhost:5001")!

// ── Local IP helper ───────────────────────────────────────────────────────────

private func localNetworkIP() -> String? {
    var ifaddr: UnsafeMutablePointer<ifaddrs>?
    guard getifaddrs(&ifaddr) == 0 else { return nil }
    defer { freeifaddrs(ifaddr) }
    var ptr = ifaddr
    while let current = ptr {
        let ifa = current.pointee
        if ifa.ifa_addr.pointee.sa_family == UInt8(AF_INET) {
            let name = String(cString: ifa.ifa_name)
            if name.hasPrefix("en") {
                var addr = ifa.ifa_addr.pointee
                var buf = [CChar](repeating: 0, count: Int(INET_ADDRSTRLEN))
                inet_ntop(AF_INET, &addr.sa_data.2, &buf, socklen_t(INET_ADDRSTRLEN))
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

    func applicationDidFinishLaunching(_ notification: Notification) {
        print("Energy Monitor started — project root: \(projectRoot.path)")
        NSApp.setActivationPolicy(.accessory) // no Dock icon; menu bar only
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

        let openItem = NSMenuItem(title: "Open Energy Monitor",
                                  action: #selector(openInBrowser),
                                  keyEquivalent: "")
        openItem.target = self
        menu.addItem(openItem)

        let copyItem = NSMenuItem(title: "Copy Local URL",
                                  action: #selector(copyLocalURL),
                                  keyEquivalent: "")
        copyItem.target = self
        menu.addItem(copyItem)

        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Quit Energy Monitor",
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
        // Flash the menu item title briefly
        statusItem?.menu?.item(at: 1)?.title = "Copied!"
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in
            self?.statusItem?.menu?.item(at: 1)?.title = "Copy Local URL"
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        return false
    }

    func applicationWillTerminate(_ notification: Notification) {
        flaskProcess?.terminate()
    }

    // ── Flask startup ─────────────────────────────────────────────────────────

    private func startFlaskServer() {
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
