import SwiftUI
import AppKit
import WebKit

// ── Project root resolution ───────────────────────────────────────────────────
//
// Derive the project directory relative to this binary so the app works
// regardless of where the project folder lives on disk.
//
// Layout:   <project>/EnergyMonitorApp/EnergyMonitorApp   (binary)
//        or <project>/EnergyMonitorApp/EnergyMonitorApp.app/Contents/MacOS/EnergyMonitorApp
//
// In both cases the project root is two or four levels up from the binary.
private func resolveProjectRoot() -> URL {
    let binaryURL = URL(fileURLWithPath: CommandLine.arguments[0]).standardizedFileURL

    // Running as .app bundle → .../EnergyMonitorApp.app/Contents/MacOS/binary
    if binaryURL.pathComponents.contains("Contents") {
        return binaryURL
            .deletingLastPathComponent() // MacOS/
            .deletingLastPathComponent() // Contents/
            .deletingLastPathComponent() // EnergyMonitorApp.app/
            .deletingLastPathComponent() // EnergyMonitorApp/
    }

    // Running as plain binary → .../EnergyMonitorApp/binary
    return binaryURL
        .deletingLastPathComponent() // EnergyMonitorApp/
        .deletingLastPathComponent() // project root
}

private let projectRoot  = resolveProjectRoot()
private let venvPython   = projectRoot.appendingPathComponent("venv/bin/python3").path
private let flaskScript  = projectRoot.appendingPathComponent("web.py").path

// ── ContentView ───────────────────────────────────────────────────────────────

struct ContentView: View {
    @State private var isLoading = true

    var body: some View {
        VStack(spacing: 0) {
            // Toolbar
            HStack {
                Text("Energy Monitor")
                    .font(.headline)
                Spacer()
                if isLoading {
                    ProgressView()
                        .scaleEffect(0.8)
                }
                Button {
                    NotificationCenter.default.post(
                        name: .reloadWebView, object: nil)
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .buttonStyle(.borderless)
                .help("Refresh dashboard")
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            .background(Color(NSColor.windowBackgroundColor))

            Divider()

            WebViewContainer(isLoading: $isLoading)
        }
        .frame(minWidth: 800, minHeight: 600)
    }
}

// ── WebView wrapper ───────────────────────────────────────────────────────────

struct WebViewContainer: NSViewRepresentable {
    @Binding var isLoading: Bool

    func makeNSView(context: Context) -> WKWebView {
        let webView = WKWebView(frame: .zero)
        webView.navigationDelegate = context.coordinator
        webView.customUserAgent =
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) " +
            "AppleWebKit/605.1.15 (KHTML, like Gecko) " +
            "Version/17.0 Safari/605.1.15"

        NotificationCenter.default.addObserver(
            forName: .reloadWebView, object: nil, queue: .main) { _ in
            Self.loadDashboard(webView)
        }

        return webView
    }

    func updateNSView(_ nsView: WKWebView, context: Context) {}

    func makeCoordinator() -> Coordinator {
        Coordinator(isLoading: $isLoading)
    }

    static func loadDashboard(_ webView: WKWebView) {
        guard let url = URL(string: "http://localhost:5001") else { return }
        let request = URLRequest(url: url, timeoutInterval: 10)
        webView.load(request)
    }

    class Coordinator: NSObject, WKNavigationDelegate {
        @Binding var isLoading: Bool

        init(isLoading: Binding<Bool>) {
            _isLoading = isLoading
        }

        func webView(_ webView: WKWebView,
                     didStartProvisionalNavigation _: WKNavigation!) {
            isLoading = true
        }

        func webView(_ webView: WKWebView,
                     didFinish _: WKNavigation!) {
            isLoading = false
        }

        func webView(_ webView: WKWebView,
                     didFail _: WKNavigation!, withError _: Error) {
            isLoading = false
        }

        func webView(_ webView: WKWebView,
                     didFailProvisionalNavigation _: WKNavigation!,
                     withError error: Error) {
            isLoading = false
        }
    }
}

// ── Notification name ─────────────────────────────────────────────────────────

extension Notification.Name {
    static let reloadWebView = Notification.Name("ReloadWebView")
}

// ── AppDelegate ───────────────────────────────────────────────────────────────

class AppDelegate: NSObject, NSApplicationDelegate {
    var flaskProcess: Process?
    var window: NSWindow?
    var statusItem: NSStatusItem?

    // How long to wait between Flask readiness probes (seconds)
    private let probeInterval: TimeInterval = 0.5
    // Maximum total time to wait before giving up and loading anyway
    private let probeTimeout: TimeInterval  = 20.0

    func applicationDidFinishLaunching(_ notification: Notification) {
        print("Energy Monitor started — project root: \(projectRoot.path)")

        startFlaskServer()
        buildAndShowWindow()
        buildStatusItem()
        waitForFlaskThenLoad()
    }

    // ── Menu-bar status item ──────────────────────────────────────────────────

    private func buildStatusItem() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        if let btn = item.button {
            btn.image = NSImage(systemSymbolName: "bolt.fill",
                                accessibilityDescription: "Energy Monitor")
            btn.image?.isTemplate = true   // adapts to light/dark menu bar
        }

        let menu = NSMenu()
        menu.addItem(NSMenuItem(title: "Show Energy Monitor",
                                action: #selector(toggleWindow),
                                keyEquivalent: ""))
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Quit Energy Monitor",
                                action: #selector(NSApplication.terminate(_:)),
                                keyEquivalent: "q"))

        item.menu = menu
        statusItem = item
    }

    @objc private func toggleWindow() {
        guard let window = window else { return }
        if window.isVisible {
            window.orderOut(nil)
            statusItem?.menu?.item(at: 0)?.title = "Show Energy Monitor"
        } else {
            NSApp.activate(ignoringOtherApps: true)
            window.makeKeyAndOrderFront(nil)
            statusItem?.menu?.item(at: 0)?.title = "Hide Energy Monitor"
        }
    }

    // ── Window management ─────────────────────────────────────────────────────

    private func buildAndShowWindow() {
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 900, height: 700),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "Energy Monitor"
        window.contentViewController = NSHostingController(rootView: ContentView())
        window.setFrameAutosaveName("EnergyMonitorMain") // remember size/position
        window.makeKeyAndOrderFront(nil)
        window.center()
        self.window = window

        // Keep "Show/Hide" label in sync when the window is closed via the red button.
        NotificationCenter.default.addObserver(
            forName: NSWindow.willCloseNotification,
            object: window,
            queue: .main
        ) { [weak self] _ in
            self?.statusItem?.menu?.item(at: 0)?.title = "Show Energy Monitor"
        }
    }

    // Re-open the window if the user clicks the Dock icon after closing it.
    func applicationShouldHandleReopen(_ sender: NSApplication,
                                       hasVisibleWindows flag: Bool) -> Bool {
        if !flag {
            NSApp.activate(ignoringOtherApps: true)
            window?.makeKeyAndOrderFront(nil)
            statusItem?.menu?.item(at: 0)?.title = "Hide Energy Monitor"
        }
        return true
    }

    // Keep the app (and Flask + poller) alive when the window is closed.
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

        process.executableURL        = URL(fileURLWithPath: venvPython)
        process.arguments            = ["web.py"]
        process.currentDirectoryURL  = projectRoot
        process.standardOutput       = pipe
        process.standardError        = pipe

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

        // Stream Flask output to the console for debugging
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

    // Probe http://localhost:5001 until it responds, then trigger a load.
    private func waitForFlaskThenLoad() {
        let deadline = Date().addingTimeInterval(probeTimeout)

        func probe() {
            guard let url = URL(string: "http://localhost:5001") else { return }
            var request = URLRequest(url: url, timeoutInterval: probeInterval)
            request.httpMethod = "HEAD"

            URLSession.shared.dataTask(with: request) { _, response, _ in
                let ready = (response as? HTTPURLResponse)?.statusCode != nil

                DispatchQueue.main.async {
                    if ready {
                        print("Flask is ready — loading dashboard")
                        NotificationCenter.default.post(name: .reloadWebView, object: nil)
                    } else if Date() < deadline {
                        DispatchQueue.main.asyncAfter(deadline: .now() + self.probeInterval) {
                            probe()
                        }
                    } else {
                        print("Flask did not respond within \(self.probeTimeout)s — loading anyway")
                        NotificationCenter.default.post(name: .reloadWebView, object: nil)
                    }
                }
            }.resume()
        }

        // Give Flask a brief head-start before the first probe
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { probe() }
    }
}

// ── Entry point ───────────────────────────────────────────────────────────────

let app      = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
_ = NSApplicationMain(CommandLine.argc, CommandLine.unsafeArgv)
