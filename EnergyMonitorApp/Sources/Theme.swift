import AppKit
import SwiftUI

/// Menu-bar popover palette: macOS system colours and fonts only, so it follows light/dark, the user's
/// accent colour, Increase Contrast and the popover's own vibrancy. (The collector's web dashboard keeps
/// its olive/stone design; the native popover deliberately does not.)
enum Theme {
    static let background = Color.clear                          // NSPopover supplies the vibrant material
    static let surface = Color.primary.opacity(0.05)              // grouped-card fill
    static let border = Color.primary.opacity(0.12)               // hairline, adapts to appearance
    static let text = Color.primary
    static let textLight = Color.secondary
    static let accent = Color.accentColor
    static let hero = Color.accentColor.opacity(0.14)
    static let heroText = Color.primary
    static let green = Color(nsColor: .systemGreen)
    static let red = Color(nsColor: .systemRed)
    static let amber = Color(nsColor: .systemOrange)

    /// Readout type: SF (not a serif); callers add `.monospacedDigit()`. Name kept for existing call sites.
    static func serif(_ size: CGFloat, weight: Font.Weight = .regular) -> Font {
        .system(size: size, weight: weight == .regular ? .semibold : weight, design: .default)
    }
}

struct ThemeCard: ViewModifier {
    var padding: CGFloat = 10
    func body(content: Content) -> some View {
        content
            .padding(padding)
            .background(Theme.surface, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).stroke(Theme.border, lineWidth: 0.5))
    }
}

extension View {
    func themeCard(padding: CGFloat = 10) -> some View { modifier(ThemeCard(padding: padding)) }
}
