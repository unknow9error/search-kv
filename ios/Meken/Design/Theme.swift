import SwiftUI

enum Theme {
    static let ink = Color("Ink")
    static let paper = Color("Paper")
    static let accent = Color("AccentColor")
    static let secondary = Color("SecondarySurface")
    static let muted = Color(red: 0.38, green: 0.44, blue: 0.41)
    static let border = Color(red: 0.88, green: 0.91, blue: 0.89)
    static let sage = Color(red: 0.91, green: 0.94, blue: 0.91)
    static let coral = Color(red: 0.80, green: 0.36, blue: 0.23)
}

struct BrandWordmark: View {
    var body: some View {
        Text("meken").font(.system(.title2, weight: .bold)).tracking(-1.2).foregroundStyle(Theme.ink)
    }
}

struct SearchFieldRow: View {
    let title: String
    let value: String
    let symbol: String
    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: symbol).foregroundStyle(Theme.accent).frame(width: 22)
            VStack(alignment: .leading, spacing: 3) {
                if !title.isEmpty { Text(title).font(.caption).foregroundStyle(Theme.muted) }
                Text(value).font(.subheadline).foregroundStyle(Theme.ink)
            }
            Spacer(minLength: 8)
            Image(systemName: "chevron.right").font(.caption).foregroundStyle(Theme.muted)
        }
        .padding(14).frame(maxWidth: .infinity, minHeight: 52, alignment: .leading)
        .background(Theme.secondary, in: .rect(cornerRadius: 10))
        .accessibilityElement(children: .combine)
    }
}

struct BrandMark: View {
    var size: CGFloat = 30
    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: size * 0.28).fill(Theme.accent)
            Image(systemName: "door.left.hand.open").font(.system(size: size * 0.52, weight: .semibold)).foregroundStyle(.white)
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }
}

struct SurfaceGroup<Content: View>: View {
    var title: String? = nil
    @ViewBuilder var content: Content
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if let title { Text(title).font(.subheadline.weight(.semibold)) }
            content
        }
        .foregroundStyle(Theme.ink).padding(16).frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.secondary, in: .rect(cornerRadius: 12))
    }
}

struct PrimaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.font(.subheadline.weight(.semibold)).padding(.horizontal, 18).frame(maxWidth: .infinity, minHeight: 48)
            .foregroundStyle(.white).background(Theme.accent.opacity(configuration.isPressed ? 0.8 : 1), in: .rect(cornerRadius: 8))
    }
}

struct NoticeCard: View {
    let text: String
    var symbol = "info.circle"
    var body: some View {
        Label(text, systemImage: symbol).font(.footnote).foregroundStyle(Theme.ink)
            .padding(14).frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.sage, in: .rect(cornerRadius: 10))
            .accessibilityElement(children: .combine)
    }
}

struct EmptyState: View {
    let symbol: String
    let title: String
    let detail: String
    var body: some View {
        ContentUnavailableView {
            Label(title, systemImage: symbol)
        } description: {
            Text(detail)
        }
    }
}

struct HouseIllustration: View {
    var body: some View {
        GeometryReader { geometry in
            let w = geometry.size.width
            ZStack(alignment: .bottom) {
                Circle().fill(Theme.coral.opacity(0.19)).frame(width: w * 0.48).offset(x: w * 0.22, y: -28)
                RoundedRectangle(cornerRadius: 70).fill(Theme.accent.opacity(0.08)).frame(width: w * 0.86, height: 100).offset(y: 20)
                HStack(alignment: .bottom, spacing: 10) {
                    building(width: w * 0.19, height: 86, color: Theme.coral.opacity(0.8), floors: 3)
                    building(width: w * 0.26, height: 155, color: Theme.accent, floors: 5)
                    building(width: w * 0.22, height: 115, color: Theme.accent.opacity(0.6), floors: 4)
                }.padding(.bottom, 10)
                Image(systemName: "tree.fill").font(.system(size: 56)).foregroundStyle(Theme.accent).offset(x: -w * 0.33, y: 5)
                Image(systemName: "tree.fill").font(.system(size: 40)).foregroundStyle(Theme.accent.opacity(0.7)).offset(x: w * 0.33, y: 5)
            }.frame(width: w, height: geometry.size.height)
        }
        .accessibilityHidden(true)
    }
    private func building(width: CGFloat, height: CGFloat, color: Color, floors: Int) -> some View {
        VStack(spacing: 8) {
            ForEach(0..<floors, id: \.self) { _ in
                HStack(spacing: 8) {
                    ForEach(0..<3, id: \.self) { _ in
                        RoundedRectangle(cornerRadius: 2).fill(.white.opacity(0.62)).frame(height: 13)
                    }
                }
            }
        }
        .padding(12).frame(width: width, height: height, alignment: .top)
        .background(color, in: UnevenRoundedRectangle(topLeadingRadius: 10, topTrailingRadius: 10))
    }
}
