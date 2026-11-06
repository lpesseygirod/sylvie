import CoreGraphics
import Foundation

/// Sylvie's orb, small (design/DESIGN.md §2.5, §3): the menu bar glyph. Same construction as the
/// web orb: soft glow, three colour blobs blended with "screen", a bright breathing core.
public enum OrbState: String, CaseIterable, Sendable {
    case idle, listening, thinking, speaking, muted

    /// Whether the glyph changes over time (the menu bar redraws only then).
    public var animates: Bool { self == .listening || self == .thinking || self == .speaking }
}

public enum OrbGlyph {
    static let lilac = (0.773, 0.608, 1.0)   // #C59BFF
    static let blue = (0.357, 0.486, 1.0)    // #5B7CFF
    static let cyan = (0.435, 0.878, 1.0)    // #6FE0FF
    static let accent = (0.557, 0.659, 1.0)  // #8EA8FF

    /// Renders the glyph at `pixels` × `pixels` (e.g. 36 for an 18 pt menu bar icon on Retina).
    /// `time` is in seconds; `level` (0…1) lets real audio drive the core when available.
    public static func render(state: OrbState, time: Double, pixels: Int = 36, level: Double = 0) -> CGImage? {
        let space = CGColorSpace(name: CGColorSpace.sRGB)!
        guard let ctx = CGContext(
            data: nil, width: pixels, height: pixels, bitsPerComponent: 8, bytesPerRow: 0, space: space,
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ) else { return nil }
        let s = CGFloat(pixels)
        let c = CGPoint(x: s / 2, y: s / 2)
        let r = s * 0.40  // orb radius; the glow reaches the edge

        if state == .muted {
            drawMuted(ctx, center: c, radius: r, size: s)
            return ctx.makeImage()
        }

        let (glow, spin, core): (Double, Double, Double) = {
            switch state {
            case .idle: return (0.18, 20, 0.96 + 0.02 * sin(time * 2 * .pi / 4))
            case .listening: return (0.45, 12, 1.0 + 0.06 * sin(time * 2 * .pi / 2.2) + level * 0.2)
            case .thinking: return (0.35, 2.6, 0.9 + 0.03 * sin(time * 2 * .pi / 1.4))
            case .speaking: return (0.5, 12, 1.0 + 0.1 * sin(time * 2 * .pi / 0.84) + level * 0.2)
            case .muted: return (0, 1, 1)
            }
        }()

        // 1. Glow
        radial(ctx, at: c, radius: s * 0.5, color: accent, alphas: [glow, glow * 0.4, 0])

        // 2. Dark body so the colours read on a light menu bar too
        ctx.saveGState()
        ctx.addEllipse(in: CGRect(x: c.x - r, y: c.y - r, width: 2 * r, height: 2 * r))
        ctx.clip()
        ctx.setFillColor(CGColor(srgbRed: 0.08, green: 0.09, blue: 0.16, alpha: state == .idle ? 0.75 : 0.9))
        ctx.fill(CGRect(x: 0, y: 0, width: s, height: s))

        // 3. Colour blobs, turning at different speeds and directions
        ctx.setBlendMode(.screen)
        let blobs: [((Double, Double, Double), Double, Double, Double)] = [
            (lilac, 0.0, 1, 1), (blue, 2.1, -16.0 / 12, 0.9), (cyan, 4.2, 20.0 / 12, 0.8),
        ]
        for (color, offset, speed, strength) in blobs {
            let angle = offset + time * 2 * .pi / spin * speed
            let p = CGPoint(x: c.x + cos(angle) * r * 0.42, y: c.y + sin(angle) * r * 0.42)
            radial(ctx, at: p, radius: r * 1.05, color: color, alphas: [strength, strength * 0.35, 0])
        }

        // 4. Bright core
        let coreR = r * 0.62 * core
        radial(ctx, at: CGPoint(x: c.x - r * 0.08, y: c.y + r * 0.08), radius: coreR,
               color: (1, 1, 1), alphas: [state == .thinking ? 0.8 : 1, 0.55, 0])
        ctx.restoreGState()

        // 5. Thin ring
        ctx.setStrokeColor(CGColor(srgbRed: accent.0, green: accent.1, blue: accent.2, alpha: 0.35))
        ctx.setLineWidth(max(1, s / 36))
        ctx.strokeEllipse(in: CGRect(x: c.x - r, y: c.y - r, width: 2 * r, height: 2 * r))
        return ctx.makeImage()
    }

    private static func drawMuted(_ ctx: CGContext, center c: CGPoint, radius r: CGFloat, size s: CGFloat) {
        let grey = CGColor(srgbRed: 0.6, green: 0.63, blue: 0.69, alpha: 0.9)
        radial(ctx, at: c, radius: r, color: (0.6, 0.63, 0.69), alphas: [0.55, 0.3, 0.12])
        ctx.setStrokeColor(grey)
        ctx.setLineWidth(max(1, s / 30))
        ctx.strokeEllipse(in: CGRect(x: c.x - r, y: c.y - r, width: 2 * r, height: 2 * r))
        ctx.setLineCap(.round)
        ctx.setLineWidth(max(1.5, s / 16))
        ctx.move(to: CGPoint(x: c.x - r * 0.95, y: c.y + r * 0.95))
        ctx.addLine(to: CGPoint(x: c.x + r * 0.95, y: c.y - r * 0.95))
        ctx.strokePath()
    }

    private static func radial(_ ctx: CGContext, at p: CGPoint, radius: CGFloat,
                               color: (Double, Double, Double), alphas: [Double]) {
        let colors = alphas.map { CGColor(srgbRed: color.0, green: color.1, blue: color.2, alpha: $0) } as CFArray
        let locations: [CGFloat] = [0, 0.5, 1]
        guard let g = CGGradient(colorsSpace: CGColorSpace(name: CGColorSpace.sRGB), colors: colors, locations: locations)
        else { return }
        ctx.drawRadialGradient(g, startCenter: p, startRadius: 0, endCenter: p, endRadius: radius, options: [])
    }
}
