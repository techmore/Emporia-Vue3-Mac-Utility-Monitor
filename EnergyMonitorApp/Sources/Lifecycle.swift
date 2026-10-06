import AppKit
import Foundation

/// Start-at-login and uninstall support shared by the menu, the popover and the CLI flags.
enum LoginItem {
    static let label = "com.dolbec.energymonitor.login"
    static let brewFormula = "techmore/tap/energy-monitor"
    static let brewPath = "/opt/homebrew/bin/brew"

    private static var agentsDirectory: URL {
        FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/LaunchAgents")
    }
    static var plistURL: URL { agentsDirectory.appendingPathComponent(label + ".plist") }
    private static var brewServicePlist: URL {
        agentsDirectory.appendingPathComponent("homebrew.mxcl.energy-monitor.plist")
    }

    static var executablePath: String { Bundle.main.executablePath ?? CommandLine.arguments[0] }
    static var isBrewInstall: Bool { executablePath.contains("/Cellar/energy-monitor/") }

    /// Homebrew installs live in a versioned Cellar folder; the `opt` link survives upgrades.
    static var stableExecutable: String {
        guard let marker = executablePath.range(of: "/Cellar/energy-monitor/") else { return executablePath }
        let rest = executablePath[marker.upperBound...]
        guard let slash = rest.firstIndex(of: "/") else { return executablePath }
        return String(executablePath[..<marker.lowerBound]) + "/opt/energy-monitor" + String(rest[slash...])
    }

    static var isEnabled: Bool {
        FileManager.default.fileExists(atPath: plistURL.path)
            || FileManager.default.fileExists(atPath: brewServicePlist.path)
    }

    /// Writes a per-user LaunchAgent. It is not loaded now (the app is already running);
    /// launchd picks it up at the next login. If the app is later removed, the agent
    /// deletes itself instead of leaving a dangling entry behind.
    static func enable() throws {
        guard !isEnabled else { return }
        let cleanup = "/bin/launchctl bootout gui/$(id -u)/\(label) 2>/dev/null; /bin/rm -f \"\(plistURL.path)\""
        let plist: [String: Any] = [
            "Label": label,
            "ProgramArguments": ["/bin/sh", "-c", "[ -x \"$0\" ] && exec \"$0\"; " + cleanup, stableExecutable],
            "RunAtLoad": true,
            "KeepAlive": false,
            "LimitLoadToSessionType": "Aqua",
            "ProcessType": "Interactive",
        ]
        try FileManager.default.createDirectory(at: agentsDirectory, withIntermediateDirectories: true)
        let data = try PropertyListSerialization.data(fromPropertyList: plist, format: .xml, options: 0)
        try data.write(to: plistURL, options: .atomic)
    }

    static func disable() {
        try? FileManager.default.removeItem(at: plistURL)
        if FileManager.default.fileExists(atPath: brewServicePlist.path) {
            // `brew services stop` also unloads the job, which may end this process.
            detached("sleep 1; \(brewPath) services stop \(brewFormula)")
        }
    }

    static func detached(_ script: String) {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/sh")
        process.arguments = ["-c", script]
        try? process.run()
    }
}

enum Uninstaller {
    /// Stops other running copies, removes start-at-login, then removes the install.
    /// `purge` also deletes the Homebrew data directory (never a source checkout).
    static func run(dataRoot: URL, purge: Bool) -> String {
        LoginItem.disable()
        let me = ProcessInfo.processInfo.processIdentifier
        for other in NSRunningApplication.runningApplications(withBundleIdentifier: "com.dolbec.energymonitor")
        where other.processIdentifier != me { other.terminate() }

        if LoginItem.isBrewInstall {
            var script = "sleep 1; \(LoginItem.brewPath) uninstall energy-monitor"
            var note = "Homebrew will remove Energy Monitor. Your data stays in \(dataRoot.path)."
            if purge && dataRoot.path.hasSuffix("/var/energy-monitor") {
                script += " && rm -rf '\(dataRoot.path)'"
                note = "Homebrew will remove Energy Monitor and its data."
            }
            LoginItem.detached(script)
            return note
        }

        var target = URL(fileURLWithPath: LoginItem.executablePath)
        while target.path != "/" && target.pathExtension != "app" { target.deleteLastPathComponent() }
        if target.pathExtension == "app" {
            try? FileManager.default.trashItem(at: target, resultingItemURL: nil)
            return "Moved \(target.lastPathComponent) to the Trash. Project files and data were not touched."
        }
        return "Nothing to remove: this copy is not an app bundle."
    }
}
