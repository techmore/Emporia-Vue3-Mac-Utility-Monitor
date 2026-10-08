import AppKit
import CryptoKit
import SQLite3
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
    enum CodingKeys: String, CodingKey {
        case version, online, currentWatts, costPerHour, recordedKwh
        // convertFromSnakeCase capitalizes the word after the numeric prefix.
        case cost24h = "cost24H"
        case monthCost, monthDaysRecorded, lastReading, topCircuits
        case panelLabel, panelSlots, breakerSlots, activeDeviceGid, collectorSourceId
    }
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
    let activeDeviceGid: String?
    let collectorSourceId: String?
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

struct StoredMenuResponse: Codable {
    let data: Data
    let fetchedAt: Date
}

struct MenuDiskCache {
    let directory: URL

    init(directory: URL? = nil) {
        self.directory = directory ?? FileManager.default.urls(
            for: .applicationSupportDirectory, in: .userDomainMask
        )[0].appendingPathComponent("EnergyMonitor/collector-cache", isDirectory: true)
    }

    func file(for endpoint: URL) -> URL {
        let digest = SHA256.hash(data: Data(endpoint.absoluteString.utf8))
            .map { String(format: "%02x", $0) }.joined()
        return directory.appendingPathComponent(digest + ".json")
    }

    func read(_ endpoint: URL) -> StoredMenuResponse? {
        let path = file(for: endpoint)
        guard let attributes = try? FileManager.default.attributesOfItem(atPath: path.path),
              let size = attributes[.size] as? NSNumber, size.intValue <= 8 * 1024 * 1024,
              let data = try? Data(contentsOf: path) else { return nil }
        return try? JSONDecoder().decode(StoredMenuResponse.self, from: data)
    }

    func write(_ data: Data, for endpoint: URL) throws {
        guard data.count <= 4 * 1024 * 1024 else { return }
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                                               attributes: [.posixPermissions: 0o700])
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: directory.path)
        let record = StoredMenuResponse(data: data, fetchedAt: Date())
        try JSONEncoder().encode(record).write(to: file(for: endpoint), options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600],
                                              ofItemAtPath: file(for: endpoint).path)
    }
}

struct DownloadedHistoryReader {
    let database: URL

    func history(channel: String, device: String, source: String, now: Date = Date()) -> (CircuitHistory, Date)? {
        var connection: OpaquePointer?
        guard sqlite3_open_v2(database.path, &connection, SQLITE_OPEN_READWRITE, nil) == SQLITE_OK,
              let connection = connection else {
            if let connection = connection { sqlite3_close(connection) }
            return nil
        }
        defer { sqlite3_close(connection) }
        sqlite3_busy_timeout(connection, 1000)
        guard sqlite3_exec(connection, "PRAGMA query_only=ON", nil, nil, nil) == SQLITE_OK else { return nil }
        guard sqlite3_exec(connection, "BEGIN", nil, nil, nil) == SQLITE_OK else { return nil }
        let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)
        func query(_ sql: String, _ values: [String] = []) -> [[String: String]]? {
            var statement: OpaquePointer?
            guard sqlite3_prepare_v2(connection, sql, -1, &statement, nil) == SQLITE_OK,
                  let statement = statement else { print("History cache SQL preparation failed: " + String(cString: sqlite3_errmsg(connection))); return nil }
            defer { sqlite3_finalize(statement) }
            for (index, value) in values.enumerated() {
                guard sqlite3_bind_text(statement, Int32(index + 1), value, -1, transient) == SQLITE_OK else { return nil }
            }
            var rows: [[String: String]] = []
            var status = sqlite3_step(statement)
            while status == SQLITE_ROW {
                var row: [String: String] = [:]
                for column in 0..<sqlite3_column_count(statement) {
                    if let text = sqlite3_column_text(statement, column) {
                        row[String(cString: sqlite3_column_name(statement, column))] = String(cString: text)
                    }
                }
                rows.append(row)
                status = sqlite3_step(statement)
            }
            return status == SQLITE_DONE ? rows : nil
        }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        guard let state = query("SELECT source_id, synchronized_at, cursor, high_watermark FROM sync_cache_state WHERE singleton=1")?.first,
              state["source_id"] == source,
              state["cursor"] == state["high_watermark"],
              let synced = state["synchronized_at"],
              let syncedAt = formatter.date(from: String(synced.prefix(19))),
              let latest = query("SELECT MAX(timestamp) AS timestamp FROM sync_cached_readings WHERE device_gid=? AND channel_name=?", [device, channel])?.first?["timestamp"] else { return nil }
        let end = formatter.string(from: now)
        var windows: [CircuitWindow] = []
        for days in [1, 7, 30] {
            let start = formatter.string(from: now.addingTimeInterval(-Double(days) * 86400))
            guard let totals = query("SELECT SUM(usage_kwh) AS kwh, SUM(cost_cents) AS cents, COUNT(*) AS readings FROM sync_cached_readings WHERE device_gid=? AND channel_name=? AND timestamp>=? AND timestamp<?", [device, channel, start, end])?.first,
                  let buckets = query("SELECT strftime(?,timestamp) AS period, SUM(usage_kwh) AS kwh FROM sync_cached_readings WHERE device_gid=? AND channel_name=? AND timestamp>=? AND timestamp<? GROUP BY period ORDER BY period", [days == 1 ? "%Y-%m-%d %H:00" : "%Y-%m-%d", device, channel, start, end]) else { return nil }
            let grouped = Dictionary(uniqueKeysWithValues: buckets.compactMap { row -> (String, Double)? in
                guard let period = row["period"], let number = row["kwh"].flatMap(Double.init) else { return nil }
                return (period, number)
            })
            let labels = DateFormatter()
            labels.locale = formatter.locale
            labels.dateFormat = days == 1 ? "yyyy-MM-dd HH:00" : "yyyy-MM-dd"
            var cursor = Calendar.current.dateInterval(of: days == 1 ? .hour : .day,
                                                       for: now.addingTimeInterval(-Double(days) * 86400))!.start
            var series: [CircuitBucket] = []
            while cursor < now {
                let label = labels.string(from: cursor)
                series.append(CircuitBucket(period: label, totalKwh: grouped[label]))
                guard let next = Calendar.current.date(byAdding: days == 1 ? .hour : .day, value: 1, to: cursor) else { return nil }
                cursor = next
            }
            windows.append(CircuitWindow(days: days, totalKwh: totals["kwh"].flatMap(Double.init),
                totalCents: totals["cents"].flatMap(Double.init), changePct: nil,
                readings: Int(totals["readings"] ?? "0") ?? 0, series: series))
        }
        return (CircuitHistory(lastReading: latest, windows: windows), syncedAt)
    }
}

final class MenuMonitor: ObservableObject {
    @Published var summary: MenuSummary?
    @Published var summaryError: String?
    @Published var selectedCircuit: MenuCircuit?
    @Published var history: CircuitHistory?
    @Published var historyError: String?
    @Published var days = 1
    @Published var refreshing = false
    @Published var autostart = false
    @Published var syncError: String?
    @Published var cachedSummaryAt: Date?
    @Published var cachedHistoryAt: Date?
    var didUpdate: (() -> Void)?
    private let baseURL: URL
    private var historyTask: URLSessionDataTask?
    private var historyGeneration = UUID()
    private let historyCachePath: String?
    private let diskCache = MenuDiskCache()

    init(baseURL: URL) {
        self.baseURL = baseURL
        let paths = UserDefaults.standard.dictionary(forKey: "collectorHistoryCachePaths") as? [String: String]
        historyCachePath = paths?[baseURL.absoluteString]
        if let stored = diskCache.read(baseURL.appendingPathComponent("api/menu-summary")),
           let decoded = try? decoder().decode(MenuSummary.self, from: stored.data) {
            summary = decoded
            cachedSummaryAt = stored.fetchedAt
            summaryError = "Cached readings; collector connection has not been verified."
        }
    }
    var online: Bool { cachedSummaryAt == nil && summaryError == nil && summary?.online == true }
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
            if result != nil, let data = data {
                do { try self.diskCache.write(data, for: request.url!) }
                catch { print("Could not cache menu summary: \(error.localizedDescription)") }
            }
            let cachedAt = result == nil ? self.diskCache.read(request.url!)?.fetchedAt : nil
            DispatchQueue.main.async {
                self.refreshing = false
                if let result = result {
                    self.summary = result
                    self.summaryError = nil
                    self.cachedSummaryAt = nil
                } else {
                    self.summaryError = "Monitor unavailable. Open Settings to check the connection."
                    self.cachedSummaryAt = cachedAt
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
        cachedHistoryAt = nil
        loadHistory()
    }

    func goBack() {
        selectedCircuit = nil
        history = nil
        historyError = nil
        cachedHistoryAt = nil
        historyGeneration = UUID()
        historyTask?.cancel()
    }

    private func loadHistory() {
        guard let circuit = selectedCircuit else { return }
        historyTask?.cancel()
        historyError = nil
        let generation = UUID()
        historyGeneration = generation
        let cachedDevice = summary?.activeDeviceGid
        let cachedSource = summary?.collectorSourceId
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
            if result != nil, let data = data {
                do { try self.diskCache.write(data, for: url) }
                catch { print("Could not cache circuit history: \(error.localizedDescription)") }
            }
            let stored = result == nil ? self.diskCache.read(url) : nil
            let cached = stored.flatMap { try? self.decoder().decode(CircuitHistory.self, from: $0.data) }
            let downloaded: (CircuitHistory, Date)?
            if result == nil, let path = self.historyCachePath,
               let device = cachedDevice, let source = cachedSource {
                downloaded = DownloadedHistoryReader(database: URL(fileURLWithPath: path))
                    .history(channel: circuit.channelName, device: device, source: source)
            } else {
                downloaded = nil
            }
            DispatchQueue.main.async {
                guard self.historyGeneration == generation else { return }
                if let result = result {
                    self.history = result
                    self.cachedHistoryAt = nil
                } else if let downloaded = downloaded {
                    self.history = downloaded.0
                    self.cachedHistoryAt = downloaded.1
                } else if let cached = cached {
                    self.history = cached
                    self.cachedHistoryAt = stored?.fetchedAt
                } else {
                    self.history = nil
                    self.cachedHistoryAt = nil
                }
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
    let toggleAutostart: () -> Void
    let uninstall: () -> Void
    let quit: () -> Void

    private func energy(_ value: Double?) -> String {
        value.map { String(format: "%.2f kWh", $0) } ?? "No data"
    }
    private func timeLabel(_ value: String?) -> String {
        guard let value = value else { return "No readings yet" }
        return "Recorded " + String(value.replacingOccurrences(of: "T", with: " ").prefix(19))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let date = monitor.cachedSummaryAt {
                Text("Cached " + date.formatted(date: .abbreviated, time: .shortened) + " - not live")
                    .font(.caption2).foregroundStyle(Theme.textLight)
            }
            if let error = monitor.syncError {
                Text(error).font(.caption2).foregroundStyle(Theme.red)
            }
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
                    Toggle("Start at Login", isOn: Binding(get: { monitor.autostart }, set: { _ in toggleAutostart() }))
                    Button("Uninstall…", action: uninstall)
                    Divider()
                    Button("Quit Energy Monitor", action: quit)
                } label: { Image(systemName: "ellipsis.circle") }
                .menuStyle(.borderlessButton).fixedSize().accessibilityLabel("Monitor options")
            }
            .buttonStyle(.borderless)
        }
        .padding(10)
        .frame(width: 440, height: 560)
        .background(Theme.background)
        .foregroundStyle(Theme.text)
    }

    private var overview: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Circle().fill(monitor.online ? Theme.green : Theme.red)
                    .frame(width: 6, height: 6)
                    .accessibilityLabel(monitor.online ? "Live readings" : "Not live")
                Text(monitor.summary?.panelLabel ?? "Service Panel")
                    .font(.caption.weight(.medium)).lineLimit(1)
                Spacer(minLength: 4)
                Text(monitor.online ? monitor.summary?.currentWatts.map { String(format: "%.0f W", $0) } ?? "—" : "—")
                    .font(Theme.serif(22)).monospacedDigit().fixedSize()
                if monitor.online, let cost = monitor.summary?.costPerHour {
                    Text(String(format: "$%.2f/hr", cost)).font(.system(size: 10)).fixedSize()
                }
            }
            .foregroundStyle(Theme.heroText)
            .padding(.horizontal, 10).padding(.vertical, 7)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.hero, in: RoundedRectangle(cornerRadius: 10))
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
            if let slots = monitor.summary?.breakerSlots, !slots.isEmpty {
                LazyVStack(spacing: 3) {
                    ForEach(slots.sorted { $0.slot < $1.slot }) { slot in breakerCard(slot) }
                }
            } else {
                Text("Waiting for panel readings…").font(.subheadline).foregroundStyle(Theme.textLight)
            }
            Text(timeLabel(monitor.summary?.lastReading)).font(.caption2).foregroundStyle(Theme.textLight)
        }
    }

    private func statCard(_ title: String, _ value: String, _ detail: String) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(alignment: .firstTextBaseline, spacing: 4) {
                Text(title).font(.system(size: 9, weight: .semibold)).foregroundStyle(Theme.textLight)
                Spacer(minLength: 0)
                Text(value).font(Theme.serif(17)).monospacedDigit().fixedSize()
            }
            Text(detail).font(.caption2).foregroundStyle(Theme.textLight).lineLimit(1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .themeCard(padding: 7)
        .accessibilityElement(children: .combine)
    }

    private func breakerCard(_ slot: MenuBreakerSlot) -> some View {
        let watts = monitor.online ? slot.watts : nil
        let active = slot.channelName != nil
        let fill = slot.loadState == "danger" ? Theme.red : slot.loadState == "warn" ? Theme.amber : Theme.accent
        let usage: Color? = !active || !monitor.online ? nil : slot.usageState == "heat" ? Theme.red : slot.usageState == "high" ? Theme.amber : nil
        let peak = active && monitor.online && slot.isPeak == true
        return Button {
            if let circuit = slot.circuit { monitor.select(circuit) }
        } label: {
            HStack(spacing: 7) {
                Text(String(format: "%02d", slot.slot))
                    .font(.caption2.monospacedDigit().weight(.medium))
                    .foregroundStyle(Theme.textLight).frame(width: 20, alignment: .leading)
                Text(slot.displayName).font(.system(size: 11, weight: .medium)).lineLimit(1)
                    .foregroundStyle(active ? Theme.text : Theme.textLight)
                if peak {
                    Image(systemName: "star.fill").font(.system(size: 8)).foregroundStyle(Theme.amber)
                        .accessibilityLabel("Top usage")
                }
                Spacer(minLength: 2)
                Text(watts.map { String(format: "%.0f W", $0) } ?? (active ? "—" : "Empty"))
                    .font(.system(size: 10).monospacedDigit()).foregroundStyle(Theme.textLight)
                    .frame(width: 55, alignment: .trailing)
                if active, let amps = slot.amps {
                    Text("\(slot.poles)P/\(amps)A").font(.system(size: 9))
                        .foregroundStyle(slot.loadState == "danger" || slot.loadState == "warn" ? fill : Theme.textLight)
                        .frame(width: 42, alignment: .trailing)
                }
                if active { Image(systemName: "chevron.right").font(.system(size: 8, weight: .semibold)).foregroundStyle(Theme.textLight.opacity(0.6)) }
            }
            .padding(.horizontal, 7).padding(.vertical, 4)
            .frame(maxWidth: .infinity, minHeight: 25, alignment: .leading)
            .overlay(alignment: .bottomLeading) {
                if let percent = slot.loadPercent, active && monitor.online {
                    GeometryReader { geometry in
                        Capsule().fill(fill)
                            .frame(width: geometry.size.width * CGFloat(max(0, min(100, percent)) / 100))
                    }.frame(height: 2).padding(.horizontal, 7)
                        .accessibilityLabel("Estimated breaker load")
                }
            }
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
            if let date = monitor.cachedHistoryAt {
                Text("Cached history " + date.formatted(date: .abbreviated, time: .shortened))
                    .font(.caption2).foregroundStyle(Theme.textLight)
            }
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
