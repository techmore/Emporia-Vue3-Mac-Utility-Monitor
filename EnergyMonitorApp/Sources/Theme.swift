import AppKit
import SwiftUI

/// Dashboard palette from `BASE_CSS` in web.py (olive/stone tokens), with dark variants.
enum Theme {
    private static func hex(_ value: UInt32) -> NSColor {
        NSColor(
            red: CGFloat((value >> 16) & 0xff) / 255,
            green: CGFloat((value >> 8) & 0xff) / 255,
            blue: CGFloat(value & 0xff) / 255,
            alpha: 1
        )
    }

    private static func dynamic(light: UInt32, dark: UInt32) -> Color {
        Color(nsColor: NSColor(name: nil) { appearance in
            appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua ? hex(dark) : hex(light)
        })
    }

    static let background = dynamic(light: 0xc4c9b0, dark: 0x1f2117)   // --bg / --olive-950
    static let surface = dynamic(light: 0xdde1d0, dark: 0x2b2e21)      // --surface
    static let border = dynamic(light: 0xa7ae8b, dark: 0x464a34)       // --border
    static let text = dynamic(light: 0x292524, dark: 0xeef0e6)         // --text
    static let textLight = dynamic(light: 0x57534e, dark: 0xa7ae8b)    // --text-light
    static let accent = dynamic(light: 0x575d3d, dark: 0x8a9269)       // --accent
    static let hero = Color(nsColor: hex(0x1f2117))                    // dark service-feed card
    static let heroText = Color(nsColor: hex(0xeef0e6))
    static let green = Color(nsColor: hex(0x5a8a5e))
    static let red = Color(nsColor: hex(0xc0392b))
    static let amber = Color(nsColor: hex(0xb07d2a))

    static func serif(_ size: CGFloat, weight: Font.Weight = .regular) -> Font {
        .system(size: size, weight: weight, design: .serif)
    }
}

struct ThemeCard: ViewModifier {
    var padding: CGFloat = 10
    func body(content: Content) -> some View {
        content
            .padding(padding)
            .background(Theme.surface, in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.border.opacity(0.6), lineWidth: 1))
    }
}

extension View {
    func themeCard(padding: CGFloat = 10) -> some View { modifier(ThemeCard(padding: padding)) }
}
