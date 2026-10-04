import AppKit
import SwiftUI

struct MenuCircuit: Decodable {
    let channelName: String
    let displayName: String
    let watts: Double?
}

struct MenuSummary: Decodable {
    let version: String
    let online: Bool
    let currentWatts: Double?
    let costPerHour: Double?
    let recordedKwh: Double?
    let lastReading: String?
    let topCircuits: [MenuCircuit]
}

struct CircuitBucket: Decodable {
    let period: String
    let totalKwh: Double?
}

struct CircuitWindow: Decodable {
    let days: Int
    let totalKwh: Double?
    let totalCents: Double?
    let changePct: Double?
    let readings: Int
    let series: [CircuitBucket]
}

struct CircuitHistory: Decodable {
    let lastReading: String?
    let windows: [CircuitWindow]
}

final class MenuMonitor: ObservableObject {
    @Published var summary: MenuSummary?
    @Published var summaryError: String?
    @Published var selectedCircuit: MenuCircuit?
    @Published var history: CircuitHistory?
    @Published var historyError: String?
    @Published var days = 1
    @Published var refreshing = false
    var didUpdate: (() -> Void)?
    private let baseURL: URL
    private var historyTask: URLSessionDataTask?
    private var historyGeneration = UUID()

    init(baseURL: URL) { self.baseURL = baseURL }
    var online: Bool { summaryError == nil && summary?.online == true }
    var window: CircuitWindow? { history?.windows.first { $0.days == days } }

    private func decoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }

    func refresh() {
        guard !refreshing else { return }
        refreshing = true
        let request = URLRequest(url: baseURL.appendingPathComponent("api/menu-summary"), timeoutInterval: 5)
        URLSession.shared.dataTask(with: request) { [weak self] data, response, _ in
            guard let self = self else { return }
            let result = (response as? HTTPURLResponse)?.statusCode == 200
                ? data.flatMap { try? self.decoder().decode(MenuSummary.self, from: $0) } : nil
            DispatchQueue.main.async {
                self.refreshing = false
                if let result = result {
                    self.summary = result
                    self.summaryError = nil
                } else {
                    self.summaryError = "Monitor unavailable. Open Settings to check the connection."
                }
                self.didUpdate?()
            }
        }.resume()
        if selectedCircuit != nil { loadHistory() }
    }

    func select(_ circuit: MenuCircuit) {
        selectedCircuit = circuit
        days = 1
        history = nil
        loadHistory()
    }

    func goBack() {
        selectedCircuit = nil
        history = nil
        historyError = nil
        historyGeneration = UUID()
        historyTask?.cancel()
    }

    private func loadHistory() {
        guard let circuit = selectedCircuit else { return }
        historyTask?.cancel()
        historyError = nil
        let generation = UUID()
        historyGeneration = generation
        var allowed = CharacterSet.urlPathAllowed
        allowed.remove(charactersIn: "/%?#")
        guard let encoded = circuit.channelName.addingPercentEncoding(withAllowedCharacters: allowed),
              let url = URL(string: baseURL.absoluteString + "/api/circuit-history/" + encoded) else {
            historyError = "Could not open this circuit."
            return
        }
        historyTask = URLSession.shared.dataTask(with: URLRequest(url: url, timeoutInterval: 8)) { [weak self] data, response, _ in
            guard let self = self else { return }
            let result = (response as? HTTPURLResponse)?.statusCode == 200
                ? data.flatMap { try? self.decoder().decode(CircuitHistory.self, from: $0) } : nil
            DispatchQueue.main.async {
                guard self.historyGeneration == generation else { return }
                self.history = result
                self.historyError = result == nil ? "History unavailable. Try Refresh." : nil
            }
        }
        historyTask?.resume()
    }
}

struct MiniEnergyChart: View {
    let series: [CircuitBucket]
    var body: some View {
        GeometryReader { geometry in
            let maximum = max(series.compactMap(\.totalKwh).max() ?? 0, 0.001)
            let width = geometry.size.width / CGFloat(max(series.count, 1))
            Path { path in
                for (index, bucket) in series.enumerated() {
                    guard let value = bucket.totalKwh else { continue }
                    let height = CGFloat(max(0, value) / maximum) * geometry.size.height
                    path.addRoundedRect(in: CGRect(x: CGFloat(index) * width, y: geometry.size.height - max(2, height), width: max(1, width - 3), height: max(2, height)), cornerSize: CGSize(width: 2, height: 2))
                }
            }.fill(Color.accentColor)
        }
        .accessibilityLabel("Recorded energy chart. Missing periods remain gaps.")
    }
}

struct MonitorPopover: View {
    @ObservedObject var monitor: MenuMonitor
    let openDashboard: () -> Void
    let openSettings: () -> Void
    let quit: () -> Void

    private func energy(_ value: Double?) -> String {
        value.map { String(format: "%.2f kWh", $0) } ?? "No data"
    }
    private func timeLabel(_ value: String?) -> String {
        guard let value = value else { return "No readings yet" }
        return "Recorded " + String(value.replacingOccurrences(of: "T", with: " ").prefix(19))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Image(systemName: "bolt.circle.fill").foregroundStyle(Color.accentColor)
                Text("Energy Monitor").font(.headline)
                Spacer()
                Circle().fill(monitor.online ? Color.green : Color.orange).frame(width: 6, height: 6)
                Text(monitor.online ? "Live" : "Offline").font(.caption).foregroundStyle(.secondary)
            }
            Divider()
            if let circuit = monitor.selectedCircuit {
                circuitView(circuit)
            } else {
                overview
            }
            Spacer(minLength: 0)
            Divider()
            HStack {
                Button("Dashboard", action: openDashboard)
                Button(action: { monitor.refresh() }) { Image(systemName: "arrow.clockwise") }
                    .help("Refresh readings").accessibilityLabel("Refresh readings")
                    .disabled(monitor.refreshing)
                Spacer()
                Menu {
                    Button("Settings", action: openSettings)
                    Button("Quit Energy Monitor", action: quit)
                } label: { Image(systemName: "ellipsis.circle") }
                .menuStyle(.borderlessButton).fixedSize().accessibilityLabel("Monitor options")
            }
            .buttonStyle(.borderless)
        }
        .padding(18)
        .frame(width: 360, height: 470)
        .background(Color(NSColor.windowBackgroundColor))
    }

    private var overview: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Text(monitor.online ? monitor.summary?.currentWatts.map { String(format: "%.0f", $0) } ?? "—" : "—")
                    .font(.system(size: 38, weight: .medium, design: .rounded)).monospacedDigit()
                Text("W").foregroundStyle(.secondary)
                Spacer()
                if monitor.online, let cost = monitor.summary?.costPerHour {
                    Text(String(format: "$%.2f/hr", cost)).font(.subheadline).foregroundStyle(.secondary)
                }
            }
            Text("Minute-average power").font(.caption).foregroundStyle(.secondary)
            HStack {
                Text("Recorded · last 24 hours").font(.caption).foregroundStyle(.secondary)
                Spacer()
                Text(energy(monitor.summary?.recordedKwh)).font(.subheadline).monospacedDigit()
            }
            if let message = monitor.summaryError {
                Text(message).font(.caption).foregroundStyle(.secondary)
            }
            Divider()
            Text("CIRCUITS").font(.caption2).foregroundStyle(.secondary)
            if let circuits = monitor.summary?.topCircuits, !circuits.isEmpty {
                ForEach(circuits, id: \.channelName) { circuit in
                    Button(action: { monitor.select(circuit) }) {
                        HStack {
                            Text(circuit.displayName).lineLimit(1)
                            Spacer()
                            Text(monitor.online ? circuit.watts.map { String(format: "%.0f W", $0) } ?? "—" : "—")
                                .monospacedDigit().foregroundStyle(.secondary)
                            Image(systemName: "chevron.right").font(.caption2).foregroundStyle(.secondary)
                        }.contentShape(Rectangle()).padding(.vertical, 4)
                    }.buttonStyle(.plain)
                    .accessibilityLabel("\(circuit.displayName), view circuit history")
                }
            } else {
                Text("Waiting for circuit readings…").font(.subheadline).foregroundStyle(.secondary)
            }
            Text(timeLabel(monitor.summary?.lastReading)).font(.caption2).foregroundStyle(.secondary)
        }
    }

    private func circuitView(_ circuit: MenuCircuit) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Button(action: { monitor.goBack() }) {
                Label("Overview", systemImage: "chevron.left")
            }.buttonStyle(.borderless)
            Text(circuit.displayName).font(.title3).lineLimit(1)
            Picker("History period", selection: $monitor.days) {
                Text("1 day").tag(1)
                Text("7 days").tag(7)
                Text("30 days").tag(30)
            }.pickerStyle(.segmented).labelsHidden()
            if let window = monitor.window {
                HStack {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Recorded usage").font(.caption).foregroundStyle(.secondary)
                        Text(energy(window.totalKwh)).font(.title3).monospacedDigit()
                    }
                    Spacer()
                    VStack(alignment: .trailing, spacing: 4) {
                        Text("Recorded cost").font(.caption).foregroundStyle(.secondary)
                        Text(window.totalCents.map { String(format: "$%.2f", $0 / 100) } ?? "No data").font(.title3).monospacedDigit()
                    }
                }
                MiniEnergyChart(series: window.series).frame(height: 75)
                HStack {
                    Text(window.series.first?.period ?? "")
                    Spacer()
                    Text(window.series.last?.period ?? "")
                }.font(.caption2).foregroundStyle(.secondary)
                if let change = window.changePct {
                    Text(String(format: "%@ %.1f%% vs previous %d days", change > 0 ? "↑" : change < 0 ? "↓" : "→", abs(change), window.days)).font(.caption)
                } else {
                    Text("Collecting history for a trend").font(.caption).foregroundStyle(.secondary)
                }
                Text("\(window.readings) readings · gaps are unrecorded periods").font(.caption2).foregroundStyle(.secondary)
            } else {
                Text(monitor.historyError ?? "Loading history…").font(.subheadline).foregroundStyle(.secondary)
            }
        }
    }
}
