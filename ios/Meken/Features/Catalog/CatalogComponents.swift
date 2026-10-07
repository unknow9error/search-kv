import SwiftUI
import MekenCore

struct CatalogHeader<Actions: View>: View {
    var title = ""; var back: (() -> Void)?; @ViewBuilder var actions: Actions
    var body: some View {
        HStack(spacing: 10) {
            if let back { Button(action: back) { Image(systemName: "arrow.left").frame(width: 36, height: 44) }.accessibilityLabel("Назад") }
            if title.isEmpty { BrandWordmark() } else { Text(title).font(.subheadline.weight(.semibold)).lineLimit(1) }
            Spacer(minLength: 4); actions
        }.foregroundStyle(Theme.ink).padding(.horizontal, 14).frame(minHeight: 48).background(.white)
    }
}
struct CatalogRow: View {
    let title: String; let value: String; var symbol: String? = nil
    var body: some View {
        HStack(spacing: 10) {
            if let symbol { Image(systemName: symbol).foregroundStyle(Theme.muted).frame(width: 20) }
            VStack(alignment: .leading, spacing: 3) {
                if !title.isEmpty { Text(title).font(.caption).foregroundStyle(Theme.muted) }
                Text(value).font(.subheadline).foregroundStyle(Theme.ink)
            }; Spacer(minLength: 8); Image(systemName: "chevron.down").font(.caption).foregroundStyle(Theme.muted)
        }.padding(12).frame(maxWidth: .infinity, minHeight: 48, alignment: .leading)
        .background(Theme.secondary, in: .rect(cornerRadius: 9))
        .accessibilityElement(children: .combine)
    }
}
struct CatalogChip: View {
    let title: String; var selected = false; var action: () -> Void
    var body: some View { Button(action: action) { Text(title).font(.caption).padding(.horizontal, 13).padding(.vertical, 10).foregroundStyle(selected ? .white : Theme.ink).background(selected ? Theme.accent : Theme.secondary, in: .capsule) }.buttonStyle(.plain) }
}
/// Lossless view crop of the reference's decorative image; no bitmap UI replaces native controls.
struct ReferenceRegion: View {
    let asset: String; let rect: CGRect; let sourceSize: CGSize; var fill = false; var topAligned = false
    var body: some View {
        GeometryReader { proxy in
            let scale = fill ? max(proxy.size.width / rect.width, proxy.size.height / rect.height) : min(proxy.size.width / rect.width, proxy.size.height / rect.height)
            ZStack(alignment: .topLeading) {
                Image(asset).resizable().frame(width: sourceSize.width * scale, height: sourceSize.height * scale)
                    .offset(x: -rect.minX * scale, y: -rect.minY * scale)
            }
            .frame(width: rect.width * scale, height: rect.height * scale, alignment: .topLeading)
            .clipped()
            .frame(width: proxy.size.width, height: proxy.size.height, alignment: topAligned ? .top : .center)
        }.clipped().allowsHitTesting(false).accessibilityHidden(true)
    }
}
struct CatalogArtwork: View {
    let project: CatalogProject
    var body: some View {
        ZStack {
            Theme.secondary
            if project.isDemo && project.externalId.hasPrefix("design-") {
                ReferenceRegion(asset: "CatalogDemoProjects", rect: CGRect(x: 34, y: project.externalId == "design-river" ? 1154 : 445, width: 783, height: project.externalId == "design-river" ? 359 : 395), sourceSize: CGSize(width: 851, height: 1847))
            } else if let image = project.images.first(where: { $0.kind == "facade" || $0.kind == "site" }), let url = Apartment.httpsURL(image.url) {
                AsyncImage(url: url) { phase in
                    if let image = phase.image { image.resizable().scaledToFill() }
                    else if phase.error != nil { unavailable }
                    else { ProgressView() }
                }
            } else { unavailable }
        }.clipped().contentShape(Rectangle()).accessibilityLabel("Изображение ЖК")
    }
    private var unavailable: some View { VStack(spacing: 8) { Image(systemName: "building.2").font(.title); Text("Изображение не опубликовано").font(.caption) }.foregroundStyle(Theme.muted) }
}
struct CatalogProjectCard: View {
    let project: CatalogProject; var compact = false; var selectable = false
    @Environment(CatalogStore.self) private var model
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ZStack(alignment: .topTrailing) {
                Button { model.openProject(project) } label: { CatalogArtwork(project: project).frame(height: compact ? 110 : 180).contentShape(Rectangle()) }.buttonStyle(.plain)
                Button { if selectable { model.select(project.id) } else { Task { await model.favorite(project) } } } label: {
                    Image(systemName: selectable ? (model.selected.contains(project.id) ? "checkmark.circle.fill" : "circle") : (model.favorites.contains(where: { $0.id == project.id }) ? "heart.fill" : "heart"))
                        .font(.system(size: 18)).foregroundStyle(Theme.accent).frame(width: 36, height: 36).background(.white, in: .circle)
                }.buttonStyle(.plain).disabled(model.favoriteBusy.contains(project.id)).padding(8)
                .accessibilityLabel(selectable ? "Выбрать \(project.name)" : "Сохранить \(project.name)")
            }
            VStack(alignment: .leading, spacing: 5) {
                Button { model.openProject(project) } label: { Text(project.name).font(.subheadline.weight(.bold)).foregroundStyle(Theme.ink).frame(maxWidth: .infinity, alignment: .leading).contentShape(Rectangle()) }.buttonStyle(.plain).accessibilityIdentifier("project-\(project.id)")
                Text("\(project.developerName ?? "Застройщик не опубликован") · \(project.district.isEmpty ? project.city : project.district)").font(.caption).foregroundStyle(Theme.muted)
                HStack(alignment: .center) {
                    VStack(alignment: .leading, spacing: 5) {
                        Text(project.displayPrice.label).font(.system(.subheadline, weight: .bold)).monospacedDigit()
                        if !compact { Text([project.buildings.first?.name, project.completion].compactMap { $0 }.joined(separator: " · ")).font(.caption).foregroundStyle(Theme.muted) }
                    }
                    if !compact && !selectable {
                        Spacer(minLength: 4)
                        Button { model.select(project.id) } label: { Label(model.selected.contains(project.id) ? "В сравнении" : "Сравнить", systemImage: "chart.bar").font(.caption2).padding(8).overlay(RoundedRectangle(cornerRadius: 6).stroke(Theme.border)) }.buttonStyle(.plain)
                        .disabled(!model.selected.contains(project.id) && model.selected.count >= 3)
                    }
                }
                Text(project.sourceLabel).font(.system(size: 11)).foregroundStyle(Theme.muted).padding(.top, 5)
            }.foregroundStyle(Theme.ink).padding(12)
        }.background(.white, in: .rect(cornerRadius: 10)).clipShape(.rect(cornerRadius: 10)).overlay(RoundedRectangle(cornerRadius: 10).stroke(Theme.border))
    }
}
struct CatalogCriteriaChips: View {
    @Environment(CatalogStore.self) private var model
    var body: some View {
        ScrollView(.horizontal) {
            HStack(spacing: 8) {
                ForEach(model.criteria.districts, id: \.self) { district in CatalogChip(title: district + " ×") { model.criteria.districts.removeAll { $0 == district }; Task { await model.search() } } }
                if model.criteria.priceMax != nil { CatalogChip(title: model.criteria.priceModeLabel + " · " + model.criteria.priceSummary + " ×") { model.criteria.priceMax = nil; Task { await model.search() } } }
                ForEach(model.criteria.stages, id: \.self) { stage in CatalogChip(title: stage == "commissioned" ? "Сданные ЖК ×" : "Строится ×") { model.criteria.stages.removeAll { $0 == stage }; Task { await model.search() } } }
            }
        }.scrollIndicators(.hidden)
    }
}
