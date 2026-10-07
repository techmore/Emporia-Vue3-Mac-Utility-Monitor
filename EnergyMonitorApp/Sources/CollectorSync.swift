import Foundation
import Security

enum CollectorSyncCredentials {
    private static let service = "com.dolbec.energymonitor.history-sync"

    private static func query(_ origin: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service, kSecAttrAccount as String: origin]
    }

    static func load(origin: String) -> String? {
        var request = query(origin)
        request[kSecReturnData as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        guard SecItemCopyMatching(request as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func save(_ token: String, origin: String) throws {
        let attributes = [kSecValueData as String: Data(token.utf8)]
        var status = SecItemUpdate(query(origin) as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            var request = query(origin)
            request[kSecValueData as String] = Data(token.utf8)
            request[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
            status = SecItemAdd(request as CFDictionary, nil)
        }
        if status != errSecSuccess {
            throw NSError(domain: NSOSStatusErrorDomain, code: Int(status))
        }
    }
}

final class CollectorSyncRunner {
    private let queue = DispatchQueue(label: "com.dolbec.energymonitor.history-sync")
    private var process: Process?

    func start(python: String, script: String, origin: URL, cache: String, token: String,
               completion: @escaping (String?) -> Void) {
        queue.async {
            guard self.process == nil else { return }
            let task = Process()
            let output = Pipe()
            task.executableURL = URL(fileURLWithPath: python)
            task.arguments = [script, "--collector", origin.absoluteString, "--cache", cache]
            task.currentDirectoryURL = URL(fileURLWithPath: cache).deletingLastPathComponent()
            var environment = ProcessInfo.processInfo.environment
            environment["ENERGY_SYNC_TOKEN"] = token
            task.environment = environment
            task.standardOutput = output
            task.standardError = output
            do {
                try task.run()
                self.process = task
            } catch {
                DispatchQueue.main.async { completion("History download could not start.") }
                return
            }
            self.queue.asyncAfter(deadline: .now() + 600) {
                if self.process === task && task.isRunning { task.terminate() }
            }
            DispatchQueue.global(qos: .utility).async {
                _ = output.fileHandleForReading.readDataToEndOfFile()
                task.waitUntilExit()
                let error = task.terminationStatus == 0 ? nil : "History download failed; saved cache retained."
                self.queue.async {
                    if self.process === task { self.process = nil }
                    DispatchQueue.main.async { completion(error) }
                }
            }
        }
    }

    func stop() {
        queue.sync {
            if self.process?.isRunning == true { self.process?.terminate() }
        }
    }
}
