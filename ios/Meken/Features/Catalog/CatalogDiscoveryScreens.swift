import SwiftUI
import MapKit
import MekenCore

struct CatalogFiltersScreen: View {
    @Environment(CatalogStore.self) private var model
    @State private var draft: ProjectCriteria
    @State private var price: String
    @State private var stagesOpen = false; @State private var amenitiesOpen = false; @State private var parametersOpen = false
    @State private var deadline = Date(); @State private var useDeadline = false; @State private var validation: String?
    init(initial: ProjectCriteria) {
        _draft = State(initialValue: initial)
        _price = State(initialValue: initial.priceMax.map { String(Double($0) / 1_000_000) } ?? "")
        _useDeadline = State(initialValue: initial.completionBefore != nil)
        let formatter = DateFormatter(); formatter.locale = Locale(identifier: "en_US_POSIX"); formatter.dateFormat = "yyyy-MM-dd"
        _deadline = State(initialValue: initial.completionBefore.flatMap { formatter.date(from: $0) } ?? Date())
    }
    var body: some View {
        VStack(spacing: 0) {
            HStack { Spacer(); Button("Закрыть") { model.back() }.font(.caption) }.padding(.horizontal, 18).frame(height: 38)
            HStack { Text("Фильтры").font(.system(.title, weight: .bold)); Spacer(); Button { draft = ProjectCriteria(city: draft.city); price = ""; validation = nil } label: { Label("Сбросить", systemImage: "arrow.clockwise").font(.caption).padding(9).background(Theme.sage, in: .capsule) } }.padding(.horizontal, 18).padding(.bottom, 16)
            ScrollView {
                VStack(spacing: 12) {
                    Menu { ForEach(model.cities, id: \.self) { city in Button(city) { draft.city = city; draft.districts = []; Task { await model.loadFacets(city: city) } } } } label: { CatalogRow(title: "Город", value: draft.city ?? "Выберите город") }
                    Menu { Button("Все районы") { draft.districts = [] }; ForEach((model.facetCity == draft.city ? model.facets?.districts : nil) ?? []) { item in Button(item.value) { draft.districts = draft.districts.contains(item.value) ? draft.districts.filter { $0 != item.value } : draft.districts + [item.value] } } } label: { CatalogRow(title: "Район", value: draft.districts.isEmpty ? "Все районы" : draft.districts.joined(separator: ", ")) }
                    Menu { Button("Любой") { draft.developerNames = [] }; ForEach((model.facetCity == draft.city ? model.facets?.developerNames : nil) ?? []) { item in Button(item.value) { draft.developerNames = [item.value] } } } label: { CatalogRow(title: "Застройщик", value: draft.developerNames.first ?? "Любой") }
                    VStack(alignment: .leading, spacing: 8) {
                        Menu { Button("Цена ЖК «от»") { draft.priceMode = "published_starting_price" }; Button("Минимум по опубликованным квартирам") { draft.priceMode = "observed_listing_minimum" } } label: { HStack { Text(draft.priceModeLabel).font(.caption); Spacer(); Image(systemName: "chevron.down") }.foregroundStyle(Theme.muted) }
                        HStack { Text("До").font(.subheadline); TextField("Не выбрана", text: $price).keyboardType(.decimalPad).font(.subheadline); Text("млн ₸").font(.subheadline) }
                        Label("Цена «от» не подтверждает цену конкретной квартиры.", systemImage: "info.circle").font(.caption).foregroundStyle(Theme.muted)
                    }.padding(12).background(Theme.secondary, in: .rect(cornerRadius: 9))
                    group("Сроки и стадия", summary: draft.stages.isEmpty && !useDeadline ? "Любые" : "Выбраны", symbol: "calendar", open: $stagesOpen) {
                        HStack { ForEach([("", "Любая"), ("under_construction", "Строится"), ("commissioned", "Сдан")], id: \.0) { key, label in CatalogChip(title: label, selected: key.isEmpty ? draft.stages.isEmpty : draft.stages.contains(key)) { draft.stages = key.isEmpty ? [] : [key] } } }
                        Toggle("Срок сдачи до", isOn: $useDeadline).font(.subheadline)
                        if useDeadline { DatePicker("Дата", selection: $deadline, displayedComponents: .date).datePickerStyle(.compact) }
                    }
                    group("Инфраструктура", summary: draft.requiredAmenities.isEmpty ? "Не выбрана" : "\(draft.requiredAmenities.count) условия", symbol: "building.2", open: $amenitiesOpen) {
                        ForEach([("school", "Школа"), ("kindergarten", "Детский сад"), ("park", "Парк"), ("transit", "Остановка")], id: \.0) { kind, title in Toggle(title, isOn: Binding(get: { draft.requiredAmenities.contains(kind) }, set: { enabled in draft.requiredAmenities.removeAll { $0 == kind }; if enabled { draft.requiredAmenities.append(kind) } })).font(.subheadline) }
                        Picker("Область", selection: $draft.amenityScope) { Text("Рядом").tag("nearby"); Text("В ЖК").tag("complex"); Text("В бигвилле").tag("bigville") }.font(.subheadline)
                        if draft.amenityScope == "nearby" { Picker("Радиус", selection: $draft.amenityRadiusM) { Text("500 м").tag(500); Text("1 км").tag(1000); Text("2 км").tag(2000) } }
                        Text("Обязательные условия требуют опубликованных данных о действующих объектах. Расстояния по прямой.").font(.caption).foregroundStyle(Theme.muted)
                    }
                    group("Параметры квартир", summary: "Комнаты, площадь, этаж", symbol: "square.split.2x2", open: $parametersOpen) {
                        ScrollView(.horizontal) { HStack { ForEach(1...8, id: \.self) { room in CatalogChip(title: "\(room)", selected: draft.rooms.contains(room)) { if draft.rooms.contains(room) { draft.rooms.removeAll { $0 == room } } else { draft.rooms.append(room) } } } } }
                        TextField("Площадь от, м²", value: $draft.areaMin, format: .number).keyboardType(.decimalPad)
                        HStack { TextField("Этаж от", value: $draft.floorMin, format: .number); TextField("Этаж до", value: $draft.floorMax, format: .number) }.keyboardType(.numberPad)
                        Text("Параметры должны совпасть у одного опубликованного лота. Цена ЖК «от» не является его бюджетом.").font(.caption).foregroundStyle(Theme.muted)
                    }
                    if let validation { Text(validation).font(.caption).foregroundStyle(.red) }
                }.padding(.horizontal, 18).padding(.bottom, 20)
            }
            Button { apply() } label: { HStack { Text(model.busy ? "Ищем ЖК" : "Показать ЖК"); Image(systemName: "arrow.right") } }.buttonStyle(PrimaryButtonStyle()).disabled(model.busy).padding(18)
        }.accessibilityIdentifier("screen-filters")
    }
    private func group<Content: View>(_ title: String, summary: String, symbol: String, open: Binding<Bool>, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 12) { Button { open.wrappedValue.toggle() } label: { CatalogRow(title: title, value: summary, symbol: symbol) }; if open.wrappedValue { content().padding(.horizontal, 12).padding(.bottom, 10) } }
    }
    private func apply() {
        let raw = price.replacingOccurrences(of: ",", with: ".").trimmingCharacters(in: .whitespaces)
        if !raw.isEmpty { guard let value = Double(raw), value.isFinite, value > 0, value <= 10000 else { validation = "Введите сумму больше нуля и не больше 10 000 млн ₸."; return }; draft.priceMax = Int((value * 1_000_000).rounded()) } else { draft.priceMax = nil }
        guard draft.areaMin.map({ $0.isFinite && $0 >= 1 && $0 <= 2000 }) ?? true, draft.floorMin.map({ (1...150).contains($0) }) ?? true, draft.floorMax.map({ (1...150).contains($0) }) ?? true else { validation = "Проверьте площадь и этажи."; return }
        if let low = draft.floorMin, let high = draft.floorMax, low > high { validation = "Этаж от не может быть выше этажа до."; return }
        if useDeadline { let formatter = DateFormatter(); formatter.dateFormat = "yyyy-MM-dd"; draft.completionBefore = formatter.string(from: deadline) } else { draft.completionBefore = nil }
        validation = nil; model.criteria = draft; Task { await model.search() }
    }
}
struct CatalogResultsScreen: View {
    @Environment(CatalogStore.self) private var model
    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 13) {
                Menu { ForEach(model.cities, id: \.self) { city in Button(city) { model.chooseCity(city); Task { await model.search() } } } } label: { Label(model.criteria.city ?? "Все города", systemImage: "mappin.and.ellipse").font(.caption).foregroundStyle(Theme.muted) }
                Text(model.criteria.districts.count == 1 ? (model.criteria.districts[0] == "Есиль" ? "ЖК в Есиле" : "ЖК · \(model.criteria.districts[0])") : "Жилые комплексы").font(.system(.title2, weight: .bold))
                Text("\(model.total) ЖК").font(.subheadline).foregroundStyle(Theme.muted)
                CatalogCriteriaChips()
                HStack(spacing: 8) {
                    Button { model.push(.filters) } label: { Label("Фильтры", systemImage: "line.3.horizontal.decrease").font(.caption) }
                    Spacer(minLength: 0)
                    Menu { Button("По цене «от»") { model.sort = "price_asc"; Task { await model.search() } }; Button("По цене ↓") { model.sort = "price_desc"; Task { await model.search() } }; Button("По названию") { model.sort = "name"; Task { await model.search() } } } label: { Label("По цене «от»", systemImage: "arrow.up.arrow.down").font(.caption) }
                    Button { model.push(.map); Task { await model.loadMap() } } label: { Label("Карта", systemImage: "map").font(.caption) }
                }.padding(10).overlay(RoundedRectangle(cornerRadius: 8).stroke(Theme.border))
                if model.busy { ProgressView("Ищем ЖК").frame(maxWidth: .infinity) }
                if model.results.isEmpty && !model.busy { EmptyState(symbol: "magnifyingglass", title: "Нет ЖК по этим условиям", detail: "Измените фильтры, сохранив важные для вас условия.") }
                ForEach(model.results) { CatalogProjectCard(project: $0) }
                if model.cursor != nil { Button("Показать ещё") { Task { await model.search(more: true) } }.frame(maxWidth: .infinity, minHeight: 44).disabled(model.busy) }
            }.padding(14)
        }.safeAreaInset(edge: .bottom) { if model.selected.count >= 2 { Button("Сравнить \(model.selected.count) ЖК") { Task { await model.compare() } }.buttonStyle(PrimaryButtonStyle()).padding(12).background(.white) } }.accessibilityIdentifier("screen-results")
    }
}
struct CatalogMapScreen: View {
    @Environment(CatalogStore.self) private var model
    @State private var position: MapCameraPosition = .automatic
    @State private var selectedId: String?
    @State private var area: ProjectBounds?
    var body: some View {
        VStack(spacing: 10) {
            HStack { Text(model.criteria.city ?? "Карта ЖК").font(.headline); Spacer(); Button("Список") { model.back() } }.padding(.horizontal, 14).padding(.top, 10)
            CatalogCriteriaChips().padding(.horizontal, 14)
            HStack { Button("Список") { model.back() }; Text("Карта").foregroundStyle(.white).padding(8).background(Theme.accent, in: .rect(cornerRadius: 6)); Spacer(); Button("Искать в этой области") { model.mapBounds = area; Task { await model.loadMap() } }.font(.caption) }.font(.caption).padding(.horizontal, 14)
            Map(position: $position, selection: $selectedId) {
                ForEach(model.mapped.filter { $0.latitude != nil && $0.longitude != nil }) { project in
                    Annotation(project.name, coordinate: CLLocationCoordinate2D(latitude: project.latitude!, longitude: project.longitude!)) {
                        Button { selectedId = project.id } label: { Text(project.displayPrice.amountKzt.map { "\(ProjectCriteria.millions($0))" } ?? "Нет цены").font(.caption.weight(.semibold)).padding(10).foregroundStyle(.white).background(Theme.accent, in: .capsule) }
                    }.tag(project.id)
                }
            }.mapStyle(.standard(elevation: .flat, pointsOfInterest: .excludingAll))
            .onMapCameraChange(frequency: .onEnd) { context in
                let c = context.region.center, d = context.region.span
                area = ProjectBounds(south: max(-90, c.latitude - d.latitudeDelta / 2), west: max(-180, c.longitude - d.longitudeDelta / 2), north: min(90, c.latitude + d.latitudeDelta / 2), east: min(180, c.longitude + d.longitudeDelta / 2))
            }
            .overlay { if model.mapped.filter({ $0.latitude != nil }).isEmpty { Text("Нет опубликованных координат для показа на карте").font(.caption).padding().background(.white, in: .rect(cornerRadius: 10)) } }
            if model.unknownCoordinates > 0 { Text("Без координат: \(model.unknownCoordinates) ЖК. Они доступны в списке.").font(.caption).foregroundStyle(Theme.muted) }
            if let project = model.mapped.first(where: { $0.id == selectedId }) ?? model.mapped.first {
                HStack(spacing: 10) { CatalogArtwork(project: project).frame(width: 85, height: 65).clipShape(.rect(cornerRadius: 8)); VStack(alignment: .leading, spacing: 4) { Text(project.name).font(.caption.weight(.bold)); Text(project.sourceLabel).font(.system(size: 10)); Text(project.displayPrice.label).font(.caption.weight(.semibold)) }; Spacer() }.padding(12).overlay(RoundedRectangle(cornerRadius: 10).stroke(Theme.border)).padding(.horizontal, 14)
                Button("Открыть ЖК") { model.openProject(project) }.buttonStyle(PrimaryButtonStyle()).padding(.horizontal, 14).padding(.bottom, 8)
            }
        }.accessibilityIdentifier("screen-map")
    }
}
