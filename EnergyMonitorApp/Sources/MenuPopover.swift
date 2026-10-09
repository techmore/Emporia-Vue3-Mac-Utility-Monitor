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
    let slotState: String?
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
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        formatter.isLenient = false
        let utcFormatter = DateFormatter()
        utcFormatter.locale = formatter.locale
        utcFormatter.calendar = formatter.calendar
        utcFormatter.timeZone = TimeZone(secondsFromGMT: 0)
        utcFormatter.dateFormat = formatter.dateFormat
        utcFormatter.isLenient = false
        guard let pattern = try? NSRegularExpression(pattern:
            #"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?(Z|[+-]\d{2}:\d{2})?$"#) else { return nil }
        func parseStamp(_ stamp: String) -> Date? {
            let range = NSRange(stamp.startIndex..<stamp.endIndex, in: stamp)
            guard let match = pattern.firstMatch(in: stamp, range: range) else { return nil }
            func part(_ index: Int) -> String? {
                guard let range = Range(match.range(at: index), in: stamp) else { return nil }
                return String(stamp[range])
            }
            guard let wall = part(1) else { return nil }
            let offset = part(3)
            let base = offset == nil ? formatter : utcFormatter
            guard let date = base.date(from: wall), base.string(from: date) == wall else { return nil }
            let fraction = part(2).flatMap { Double("0." + $0) } ?? 0
            var seconds = 0.0
            if let offset = offset, offset != "Z" {
                guard let hours = Int(offset.dropFirst().prefix(2)), hours < 24,
                      let minutes = Int(offset.suffix(2)), minutes < 60 else { return nil }
                seconds = Double(hours * 3600 + minutes * 60) * (offset.hasPrefix("-") ? -1 : 1)
            }
            return date.addingTimeInterval(fraction - seconds)
        }
        func utcStamp(_ date: Date) -> String? {
            let value = (date.timeIntervalSince1970 * 1_000_000).rounded()
            guard value.isFinite, value > Double(Int64.min), value < Double(Int64.max) else { return nil }
            let total = Int64(value)
            var seconds = total / 1_000_000, fraction = total % 1_000_000
            if fraction < 0 { seconds -= 1; fraction += 1_000_000 }
            return utcFormatter.string(from: Date(timeIntervalSince1970: Double(seconds)))
                + String(format: ".%06lld+00:00", fraction)
        }
        guard let policyTable = query("SELECT name FROM sqlite_master WHERE type='table' AND name='sync_cache_format'") else { return nil }
        var policy: [String: String]?
        if !policyTable.isEmpty {
            guard let rows = query("SELECT timestamp_format,reporting_timezone,measurement_model FROM sync_cache_format WHERE singleton=1") else { return nil }
            policy = rows.first
        }
        if let policy = policy {
            guard policy["measurement_model"] == "interval_v1",
                  ["utc_v1", "legacy_local_v1"].contains(policy["timestamp_format"] ?? "") else { return nil }
            if policy["timestamp_format"] == "legacy_local_v1" && policy["reporting_timezone"] != nil { return nil }
        }
        let utc = policy?["timestamp_format"] == "utc_v1"
        let table = utc ? "sync_cached_utc_readings" : "sync_cached_readings"
        guard let state = query("SELECT source_id, synchronized_at, cursor, high_watermark FROM sync_cache_state WHERE singleton=1")?.first,
              state["source_id"] == source,
              state["cursor"] == state["high_watermark"],
              let synced = state["synchronized_at"], let syncedAt = parseStamp(synced) else { return nil }
        if utc {
            guard let zone = policy?["reporting_timezone"], let timezone = TimeZone(identifier: zone),
                  let end = utcStamp(now), let parsedSync = parseStamp(synced), utcStamp(parsedSync) == synced,
                  let latest = query("SELECT MAX(timestamp) AS timestamp FROM \(table) WHERE device_gid=? AND channel_name=? AND timestamp<?", [device, channel, end])?.first?["timestamp"] else { return nil }
            var calendar = Calendar(identifier: .gregorian)
            calendar.timeZone = timezone
            let labels = DateFormatter()
            labels.locale = formatter.locale
            labels.calendar = calendar
            labels.timeZone = timezone
            var windows: [CircuitWindow] = []
            for days in [1, 7, 30] {
                let startDate = now.addingTimeInterval(-Double(days) * 86400)
                guard let start = utcStamp(startDate),
                      let totals = query("SELECT SUM(usage_kwh) AS kwh, SUM(cost_cents) AS cents, COUNT(*) AS readings FROM \(table) WHERE device_gid=? AND channel_name=? AND timestamp>=? AND timestamp<?", [device, channel, start, end])?.first,
                      let rows = query("SELECT timestamp, usage_kwh FROM \(table) WHERE device_gid=? AND channel_name=? AND timestamp>=? AND timestamp<? ORDER BY timestamp", [device, channel, start, end]) else { return nil }
                func bucketStart(_ date: Date) -> Date {
                    let day = calendar.startOfDay(for: date)
                    return days == 1 ? day.addingTimeInterval(floor(date.timeIntervalSince(day) / 3600) * 3600) : day
                }
                var grouped: [String: Double] = [:]
                for row in rows {
                    guard let stamp = row["timestamp"], let date = parseStamp(stamp), utcStamp(date) == stamp,
                          let key = utcStamp(bucketStart(date)) else { return nil }
                    if let raw = row["usage_kwh"] {
                        guard let kwh = Double(raw), kwh.isFinite else { return nil }
                        let sum = (grouped[key] ?? 0) + kwh
                        guard sum.isFinite else { return nil }
                        grouped[key] = sum
                    }
                }
                labels.dateFormat = days == 1 ? "yyyy-MM-dd HH:mm ZZZZZ" : "yyyy-MM-dd"
                var cursor = bucketStart(startDate), series: [CircuitBucket] = []
                while cursor < now {
                    guard let key = utcStamp(cursor),
                          let boundary = calendar.date(byAdding: .day, value: 1, to: calendar.startOfDay(for: cursor)) else { return nil }
                    series.append(CircuitBucket(period: labels.string(from: cursor), totalKwh: grouped[key]))
                    let next = days == 1 ? min(cursor.addingTimeInterval(3600), boundary) : boundary
                    guard next > cursor else { return nil }
                    cursor = next
                }
                let totalKwh = totals["kwh"].flatMap(Double.init), totalCents = totals["cents"].flatMap(Double.init)
                guard (totals["kwh"] == nil || totalKwh?.isFinite == true),
                      (totals["cents"] == nil || totalCents?.isFinite == true) else { return nil }
                windows.append(CircuitWindow(days: days, totalKwh: totalKwh,
                    totalCents: totalCents, changePct: nil,
                    readings: Int(totals["readings"] ?? "0") ?? 0, series: series))
            }
            return (CircuitHistory(lastReading: latest, windows: windows), syncedAt)
        }
        guard let latest = query("SELECT MAX(timestamp) AS timestamp FROM sync_cached_readings WHERE device_gid=? AND channel_name=?", [device, channel])?.first?["timestamp"] else { return nil }
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
                Circle().fill(monitor.online ? (monitor.summary?.currentWatts != nil ? Theme.green : Theme.amber) : Theme.red)
                    .frame(width: 6, height: 6)
                    .accessibilityLabel(monitor.online ? (monitor.summary?.currentWatts != nil ? "Live minute-average power" : "Power unavailable") : "Not live")
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
                LazyVGrid(columns: [GridItem(.flexible(), spacing: 5),
                                    GridItem(.flexible(), spacing: 5)], spacing: 3) {
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
            HStack(spacing: 3) {
                Text(String(format: "%02d", slot.slot))
                    .font(.caption2.monospacedDigit().weight(.medium))
                    .foregroundStyle(Theme.textLight).frame(width: 16, alignment: .leading)
                Text(slot.displayName).font(.system(size: 11, weight: .medium)).lineLimit(1)
                    .foregroundStyle(active ? Theme.text : Theme.textLight)
                if peak {
                    Image(systemName: "star.fill").font(.system(size: 8)).foregroundStyle(Theme.amber)
                        .accessibilityLabel("Top usage")
                }
                Spacer(minLength: 2)
                Text(watts.map { String(format: "%.0f W", $0) } ?? (active ? "—" : slot.slotState == "unmonitored" ? "Unmon." : "Empty"))
                    .font(.system(size: 10).monospacedDigit()).foregroundStyle(Theme.textLight)
                    .frame(width: 40, alignment: .trailing)
                if let amps = slot.amps {
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
        .accessibilityLabel(active ? "Slot \(slot.slot), \(slot.displayName), \(peak ? "top usage, " : "")\(watts.map { String(format: "%.0f watts", $0) } ?? "power unavailable"), view circuit history" : "Slot \(slot.slot), empty")
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
