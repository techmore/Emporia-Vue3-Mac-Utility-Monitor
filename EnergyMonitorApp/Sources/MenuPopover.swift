import AppKit
import SwiftUI

struct MenuCircuit: Decodable {
    let channelName: String
    let displayName: String
    let watts: Double?
}

struct MenuBreakerSlot: Decodable, Identifiable {
    let slot: Int
    let channelName: String?
    let displayName: String
    let amps: Int?
    let poles: Int
    let watts: Double?
    let loadPercent: Double?
    let loadState: String?
    let usageState: String?
    let isPeak: Bool?
    var id: Int { slot }
    var circuit: MenuCircuit? {
        guard let channelName = channelName else { return nil }
        return MenuCircuit(channelName: channelName, displayName: displayName, watts: watts)
    }
}

struct MenuSummary: Decodable {
    let version: String
    let online: Bool
    let currentWatts: Double?
    let costPerHour: Double?
    let recordedKwh: Double?
    let cost24h: Double?
    let monthCost: Double?
    let monthDaysRecorded: Int?
    let lastReading: String?
    let topCircuits: [MenuCircuit]
    let panelLabel: String
    let panelSlots: Int
    let breakerSlots: [MenuBreakerSlot]
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
            }.fill(Theme.accent)
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
                Image(systemName: "bolt.fill").font(.caption).foregroundStyle(Theme.heroText)
                    .frame(width: 24, height: 24).background(Theme.accent, in: RoundedRectangle(cornerRadius: 6))
                Text("Energy").font(Theme.serif(19, weight: .bold)).foregroundStyle(Theme.text)
                Spacer()
                Circle().fill(monitor.online ? Theme.green : Theme.red).frame(width: 8, height: 8)
                    .shadow(color: (monitor.online ? Theme.green : Theme.red).opacity(0.35), radius: 3)
                Text(monitor.online ? "Live" : "Offline").font(.caption.weight(.medium)).foregroundStyle(Theme.textLight)
            }
            Divider()
            ScrollView {
                if let circuit = monitor.selectedCircuit {
                    circuitView(circuit)
                } else {
                    overview
                }
            }
            .scrollIndicators(.hidden)
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
        .frame(width: 440, height: 560)
        .background(Theme.background)
        .foregroundStyle(Theme.text)
    }

    private var overview: some View {
        VStack(alignment: .leading, spacing: 11) {
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text("SERVICE FEED").font(.caption2.weight(.semibold)).tracking(1.1)
                    Spacer()
                    Text(monitor.summary?.panelLabel ?? "Service Panel").font(.caption2).lineLimit(1)
                }.foregroundStyle(Theme.heroText.opacity(0.7))
                HStack(alignment: .firstTextBaseline, spacing: 5) {
                    Text(monitor.online ? monitor.summary?.currentWatts.map { String(format: "%.0f", $0) } ?? "—" : "—")
                        .font(Theme.serif(40)).monospacedDigit()
                    Text("W").font(Theme.serif(15)).foregroundStyle(Theme.heroText.opacity(0.7))
                    Spacer()
                    if monitor.online, let cost = monitor.summary?.costPerHour {
                        Text(String(format: "$%.2f/hr", cost)).font(.caption).foregroundStyle(Theme.heroText.opacity(0.7))
                    }
                    Image(systemName: "bolt.fill").font(.caption)
                }.foregroundStyle(Theme.heroText)
            }
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.hero, in: RoundedRectangle(cornerRadius: 14))
            if !monitor.online {
                Label(monitor.summaryError ?? "Poller offline or stale — showing last recorded values",
                      systemImage: "exclamationmark.triangle.fill")
                    .font(.caption).foregroundStyle(Theme.red)
            }
            HStack(spacing: 8) {
                statCard("24H COST", monitor.summary?.cost24h.map { String(format: "$%.2f", $0) } ?? "—",
                         energy(monitor.summary?.recordedKwh))
                statCard("MONTH TO DATE", monitor.summary?.monthCost.map { String(format: "$%.2f", $0) } ?? "—",
                         (monitor.summary?.monthDaysRecorded).map { "\($0) day\($0 == 1 ? "" : "s") recorded" } ?? "No data")
            }
            HStack {
                Text("CIRCUIT BREAKERS").font(.caption2.weight(.semibold)).tracking(1.1)
                Spacer()
                Text("\(monitor.summary?.panelSlots ?? 0) slots").font(.caption2).foregroundStyle(Theme.textLight)
            }
            if let slots = monitor.summary?.breakerSlots, !slots.isEmpty {
                LazyVGrid(columns: [GridItem(.flexible(), spacing: 6), GridItem(.flexible(), spacing: 6)], spacing: 6) {
                    ForEach(slots) { slot in breakerCard(slot) }
                }
            } else {
                Text("Waiting for panel readings…").font(.subheadline).foregroundStyle(Theme.textLight)
            }
            Text(timeLabel(monitor.summary?.lastReading)).font(.caption2).foregroundStyle(Theme.textLight)
        }
    }

    private func statCard(_ title: String, _ value: String, _ detail: String) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title).font(.caption2.weight(.semibold)).tracking(1.0).foregroundStyle(Theme.textLight)
            Text(value).font(Theme.serif(24)).monospacedDigit()
            Text(detail).font(.caption2).foregroundStyle(Theme.textLight).lineLimit(1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .themeCard(padding: 11)
        .accessibilityElement(children: .combine)
    }

    private func breakerCard(_ slot: MenuBreakerSlot) -> some View {
        let watts = monitor.online ? slot.watts : nil
        let active = slot.channelName != nil
        let fill = slot.loadState == "danger" ? Theme.red : slot.loadState == "warn" ? Theme.amber : Theme.accent
        let usage: Color? = !active ? nil : slot.usageState == "heat" ? Theme.red : slot.usageState == "high" ? Theme.amber : nil
        let peak = active && slot.isPeak == true
        return Button {
            if let circuit = slot.circuit { monitor.select(circuit) }
        } label: {
            HStack(spacing: 7) {
                Text(String(format: "%02d", slot.slot))
                    .font(.caption2.monospacedDigit().weight(.medium))
                    .foregroundStyle(Theme.textLight).frame(width: 20, alignment: .leading)
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 3) {
                        Text(slot.displayName).font(.caption.weight(.medium)).lineLimit(1)
                            .foregroundStyle(active ? Theme.text : Theme.textLight)
                        if peak {
                            Image(systemName: "star.fill").font(.system(size: 8)).foregroundStyle(Theme.amber)
                                .accessibilityLabel("Top usage")
                        }
                    }
                    HStack(spacing: 3) {
                        Text(watts.map { String(format: "%.0f W", $0) } ?? (active ? "—" : "Empty"))
                            .monospacedDigit()
                        if active, let amps = slot.amps { Text("· \(slot.poles)P/\(amps)A") }
                    }.font(.system(size: 10)).foregroundStyle(Theme.textLight).lineLimit(1)
                    if let percent = slot.loadPercent, active {
                        GeometryReader { geometry in
                            ZStack(alignment: .leading) {
                                Capsule().fill(Theme.border.opacity(0.35))
                                Capsule().fill(fill).frame(width: geometry.size.width * CGFloat(min(100, percent) / 100))
                            }
                        }.frame(height: 3).accessibilityLabel("Estimated breaker load")
                    }
                }
                Spacer(minLength: 0)
                if active { Image(systemName: "chevron.right").font(.system(size: 8, weight: .semibold)).foregroundStyle(Theme.textLight.opacity(0.6)) }
            }
            .padding(.horizontal, 7).padding(.vertical, 6)
            .frame(maxWidth: .infinity, minHeight: 48, alignment: .leading)
            .background(Theme.surface.opacity(active ? 1 : 0.5), in: RoundedRectangle(cornerRadius: 8))
            .background((usage ?? .clear).opacity(0.12), in: RoundedRectangle(cornerRadius: 8))
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(usage ?? Theme.border.opacity(active ? 0.6 : 0.3), lineWidth: usage == nil ? 1 : 1.5))
        }
        .buttonStyle(.plain)
        .disabled(!active)
        .accessibilityLabel(active ? "Slot \(slot.slot), \(slot.displayName), \(peak ? "top usage, " : "")\(watts.map { String(format: "%.0f watts", $0) } ?? "offline"), view circuit history" : "Slot \(slot.slot), empty")
    }

    private func circuitView(_ circuit: MenuCircuit) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Button(action: { monitor.goBack() }) {
                Label("Overview", systemImage: "chevron.left")
            }.buttonStyle(.borderless)
            Text(circuit.displayName).font(Theme.serif(22)).lineLimit(1)
            Picker("History period", selection: $monitor.days) {
                Text("1 day").tag(1)
                Text("7 days").tag(7)
                Text("30 days").tag(30)
            }.pickerStyle(.segmented).labelsHidden()
            if let window = monitor.window {
                HStack {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Recorded usage").font(.caption).foregroundStyle(Theme.textLight)
                        Text(energy(window.totalKwh)).font(Theme.serif(22)).monospacedDigit()
                    }
                    Spacer()
                    VStack(alignment: .trailing, spacing: 4) {
                        Text("Recorded cost").font(.caption).foregroundStyle(Theme.textLight)
                        Text(window.totalCents.map { String(format: "$%.2f", $0 / 100) } ?? "No data").font(Theme.serif(22)).monospacedDigit()
                    }
                }
                MiniEnergyChart(series: window.series).frame(height: 75)
                HStack {
                    Text(window.series.first?.period ?? "")
                    Spacer()
                    Text(window.series.last?.period ?? "")
                }.font(.caption2).foregroundStyle(Theme.textLight)
                if let change = window.changePct {
                    Text(String(format: "%@ %.1f%% vs previous %d days", change > 0 ? "↑" : change < 0 ? "↓" : "→", abs(change), window.days)).font(.caption)
                } else {
                    Text("Collecting history for a trend").font(.caption).foregroundStyle(Theme.textLight)
                }
                Text("\(window.readings) readings · gaps are unrecorded periods").font(.caption2).foregroundStyle(Theme.textLight)
            } else {
                Text(monitor.historyError ?? "Loading history…").font(.subheadline).foregroundStyle(Theme.textLight)
            }
        }
    }
}
