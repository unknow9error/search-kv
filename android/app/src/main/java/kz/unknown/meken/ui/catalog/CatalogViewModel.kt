package kz.unknown.meken.ui.catalog

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import java.util.UUID
import java.text.NumberFormat
import java.util.Locale
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.*
import kz.unknown.meken.core.API_JSON
import kz.unknown.meken.core.AppConfig
import kz.unknown.meken.core.safeHttpsUrl
import kz.unknown.meken.core.catalog.*
import kz.unknown.meken.data.MekenRepository
import kz.unknown.meken.data.storageHash
import kz.unknown.meken.data.userMessage

internal enum class CatalogPage { SEARCH, FILTERS, RESULTS, MAP, CONVERSATION, PROJECT, LAYOUT, COMPARISON, FAVORITES, HISTORY, PROFILE, HELP, SOURCES, REPORT }
internal data class CatalogRoute(val page: CatalogPage, val key: String = UUID.randomUUID().toString())
internal data class CatalogState(
    val config: AppConfig? = null, val criteria: ProjectCriteria = ProjectCriteria(), val facets: ProjectFacets? = null, val facetCity: String? = null,
    val results: List<CatalogProject> = emptyList(), val total: Int = 0, val cursor: String? = null, val sort: String = "price_asc",
    val mapped: List<CatalogProject> = emptyList(), val bounds: ProjectBounds? = null, val unknownCoordinates: Int = 0,
    val favorites: List<CatalogProject> = emptyList(), val selection: Set<String> = emptySet(), val favoriteBusy: Set<String> = emptySet(),
    val comparison: ProjectComparison? = null, val project: CatalogProject? = null, val layout: ProjectLayout? = null,
    val conversations: List<ProjectConversation> = emptyList(), val turns: List<ProjectHistoryTurn> = emptyList(), val conversationId: String? = null,
    val historyCursor: String? = null, val suggestions: List<String> = emptyList(), val sources: List<CatalogSource> = emptyList(),
    val busy: Boolean = false, val ready: Boolean = false, val offline: Boolean = false, val error: String? = null,
    val tab: Int = 0, val paths: List<List<CatalogRoute>> = listOf(listOf(CatalogRoute(CatalogPage.SEARCH)), listOf(CatalogRoute(CatalogPage.FAVORITES)), listOf(CatalogRoute(CatalogPage.PROFILE))),
    val pending: PendingProjectMessage? = null, val scope: Int = 0,
) {
    val route get() = paths[tab].last()
    val page get() = route.page
    val supported get() = config?.capabilities?.contains("project_catalog") == true
    val tabsVisible get() = page in listOf(CatalogPage.SEARCH,CatalogPage.RESULTS,CatalogPage.MAP,CatalogPage.FAVORITES,CatalogPage.PROFILE)
}
@Serializable internal data class PendingProjectMessage(val clientConversationId: String, val conversationId: String?, val clientTurnId: String, val message: String, val criteria: ProjectCriteria)
@Serializable internal data class ProjectCache(val config: AppConfig, val favorites: List<CatalogProject>)
@Serializable private data class CreateConversation(val criteria: ProjectCriteria, val client_conversation_id: String)
@Serializable private data class SendTurn(val client_turn_id: String, val message: String, val criteria: ProjectCriteria)
@Serializable private data class CompareBody(val project_ids: List<String>)
@Serializable private data class ReportBody(val client_report_id: String, val project_id: String, val category: String, val message: String)
@Serializable private data class ReportReceipt(val status: String)

internal class CatalogViewModel(private val repo: MekenRepository) : ViewModel() {
    private val mutable = MutableStateFlow(CatalogState())
    val state = mutable.asStateFlow()
    private val api get() = repo.api
    private var generation = 0
    private var facetGeneration = 0
    private var searchGeneration = 0
    private var searchJob: Job? = null
    private fun record(kind: String): String = "catalog.$kind.${storageHash(api.currentUserId() ?: error("No session"))}"
    fun push(page: CatalogPage) { if(page == CatalogPage.FILTERS) facets(); mutable.update { s -> s.copy(paths = s.paths.mapIndexed { index, path -> if (index == s.tab) path + CatalogRoute(page) else path }, error = null) } }
    fun back() = mutable.update { s -> s.copy(paths = s.paths.mapIndexed { index, path -> if (index == s.tab && path.size > 1) path.dropLast(1) else path }, error = null) }
    fun tab(value: Int) = mutable.update { it.copy(tab = value, error = null) }
    fun criteria(value: ProjectCriteria) = mutable.update { it.copy(criteria = value) }
    fun city(value: String) { criteria(state.value.criteria.copy(city = value, districts = emptyList())); facets() }
    fun showError(value: String) = mutable.update { it.copy(error = value) }
    fun clearScope() { generation++; searchGeneration++; searchJob?.cancel(); mutable.value = CatalogState(scope = generation) }
    private fun work(busy: Boolean = false, block: suspend (Int) -> Unit) {
        val scope = generation
        if (busy) mutable.update { it.copy(busy = true, error = null) }
        viewModelScope.launch {
            try { block(scope) }
            catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Throwable) { if (kz.unknown.meken.BuildConfig.DEBUG) android.util.Log.w("MekenCatalog", "Catalog request failed", error); if (scope == generation) mutable.update { it.copy(error = userMessage(error)) } }
            finally { if (scope == generation && busy) mutable.update { it.copy(busy = false) } }
        }
    }
    private fun catalogWork(busy: Boolean = false, block: suspend (Int) -> Unit) {
        if (!state.value.supported || state.value.offline) return
        work(busy, block)
    }
    fun initialize() = work(true) { scope ->
        api.bootstrap()
        val cache = withContext(Dispatchers.IO) { repo.files.read(record("favorites"))?.let { API_JSON.decodeFromString<ProjectCache>(it) } }
        if (scope != generation) return@work
        cache?.let { mutable.update { s -> s.copy(config = it.config, favorites = it.favorites) } }
        val pending = withContext(Dispatchers.IO) { repo.files.read(record("pending"))?.let { API_JSON.decodeFromString<PendingProjectMessage>(it) } }
        try {
            val config = api.call<AppConfig>("v1/config", authenticated = false)
            if (scope != generation) return@work
            val city = state.value.criteria.city?.takeIf { it in config.cities } ?: config.cities.firstOrNull { it == "Астана" } ?: config.cities.firstOrNull()
            mutable.update { it.copy(config = config, criteria = it.criteria.copy(city = city), ready = true, offline = false, pending = pending) }
            if (state.value.supported) { facets(); favorites(); history() }
        } catch (error: Throwable) {
            if (error is CancellationException) throw error
            if (scope == generation) mutable.update { it.copy(ready = it.config != null, offline = true, error = userMessage(error)) }
        }
    }
    fun facets(city: String? = state.value.criteria.city) { facetGeneration++; val revision = facetGeneration; catalogWork { scope ->
        val value = api.call<ProjectFacets>("v1/projects/facets" + (city?.let { "?city=" + java.net.URLEncoder.encode(it,"UTF-8") } ?: ""))
        if (scope == generation && revision == facetGeneration) mutable.update { it.copy(facets = value, facetCity = city) }
    } }
    fun search(more: Boolean = false, sort: String = state.value.sort) {
        if (!state.value.supported || state.value.offline) return
        val request = state.value.criteria; val cursor = if (more) state.value.cursor else null; val scope = generation
        searchJob?.cancel(); searchGeneration++; val revision = searchGeneration
        mutable.update { it.copy(busy = true, error = null, sort = sort) }
        searchJob = viewModelScope.launch {
            try {
                val page = api.call<ProjectPage>("v1/projects/search","POST",API_JSON.encodeToString(ProjectRequest(request,cursor = cursor,sort = sort)))
                if (scope != generation || revision != searchGeneration || state.value.criteria != request) return@launch
                mutable.update { it.copy(results = if (more) (it.results + page.items).distinctBy { p -> p.id } else page.items, total = page.total, cursor = page.nextCursor, unknownCoordinates = page.unknownCoordinatesCount) }
                if (state.value.page == CatalogPage.FILTERS) back()
                if (state.value.page != CatalogPage.RESULTS) push(CatalogPage.RESULTS)
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Throwable) { if (scope == generation && revision == searchGeneration) mutable.update { it.copy(error = userMessage(error)) } }
            finally { if (scope == generation && revision == searchGeneration) mutable.update { it.copy(busy = false) } }
        }
    }
    fun map(bounds: ProjectBounds? = state.value.bounds) = catalogWork { scope ->
        val request = state.value.criteria.copy(bounds = bounds)
        val page = api.call<ProjectPage>("v1/projects/map","POST",API_JSON.encodeToString(ProjectRequest(request,sort = state.value.sort)))
        if (scope == generation) mutable.update { it.copy(mapped = page.items, bounds = bounds, unknownCoordinates = page.unknownCoordinatesCount, error = null) }
    }
    fun project(value: CatalogProject) {
        mutable.update { it.copy(project = value) }; push(CatalogPage.PROJECT)
        catalogWork { scope -> val project = api.call<CatalogProject>("v1/projects/${value.id}"); if (scope == generation && state.value.project?.id == value.id) mutable.update { it.copy(project = project) } }
    }
    fun layout(value: ProjectLayout) { mutable.update { it.copy(layout = value) }; push(CatalogPage.LAYOUT) }
    fun refreshLayout() = catalogWork { scope -> state.value.layout?.let { value -> val fresh = api.call<ProjectLayout>("v1/project-layouts/${value.id}"); if (scope == generation) mutable.update { it.copy(layout = fresh) } } }
    fun favorites() = catalogWork { scope ->
        val items = api.call<ProjectItems<CatalogProject>>("v1/projects/favorites").items
        if (scope == generation) { mutable.update { it.copy(favorites = items) }; saveFavorites() }
    }
    private suspend fun saveFavorites() { state.value.config?.let { config -> val payload = API_JSON.encodeToString(ProjectCache(config,state.value.favorites)); withContext(Dispatchers.IO) { repo.files.write(record("favorites"),payload) } } }
    fun favorite(project: CatalogProject) {
        if (!state.value.supported || project.id in state.value.favoriteBusy || state.value.offline) return
        val remove = state.value.favorites.any { it.id == project.id }
        mutable.update { it.copy(favoriteBusy = it.favoriteBusy + project.id) }
        work { scope ->
            try {
                api.perform("v1/projects/favorites/${project.id}",if (remove) "DELETE" else "PUT")
                if (scope == generation) { mutable.update { it.copy(favorites = if (remove) it.favorites.filterNot { p -> p.id == project.id } else listOf(project)+it.favorites) }; saveFavorites() }
            } finally { if (scope == generation) mutable.update { it.copy(favoriteBusy = it.favoriteBusy - project.id) } }
        }
    }
    fun select(id: String) = mutable.update { it.copy(selection = if (id in it.selection) it.selection-id else if (it.selection.size < 3) it.selection+id else it.selection) }
    fun compare() { if (state.value.selection.size < 2) return; catalogWork(true) { scope ->
        val comparison = api.call<ProjectComparison>("v1/projects/compare","POST",API_JSON.encodeToString(CompareBody(state.value.selection.toList())))
        if (scope == generation) {
            val projects = comparison.projects.map { summary ->
                val detail = api.call<CatalogProject>("v1/projects/${summary.id}")
                if (detail.version == summary.version) summary.copy(buildings = detail.buildings) else summary
            }
            if (scope == generation) { mutable.update { it.copy(comparison = comparison.copy(projects = projects)) }; push(CatalogPage.COMPARISON) }
        }
    } }
    fun history() = catalogWork { scope -> val items = api.call<ProjectItems<ProjectConversation>>("v1/projects/conversations").items; if (scope == generation) mutable.update { it.copy(conversations = items) } }
    fun restore(conversation: ProjectConversation, more: Boolean = false) = catalogWork(true) { scope ->
        val suffix = if (more) state.value.historyCursor?.let { "?before=$it" }.orEmpty() else ""
        val history = api.call<ProjectConversationHistory>("v1/projects/conversations/${conversation.id}$suffix")
        if (scope != generation) return@catalogWork
        mutable.update { it.copy(conversationId = history.id, turns = if (more) history.turns + it.turns else history.turns, criteria = if (more) it.criteria else history.criteria, historyCursor = history.nextBefore, results = if (more) it.results else history.turns.lastOrNull()?.response?.results?.items.orEmpty(), suggestions = history.turns.lastOrNull()?.response?.suggestions.orEmpty()) }
        if (!more && state.value.page != CatalogPage.CONVERSATION) push(CatalogPage.CONVERSATION)
    }
    fun deleteConversation(id: String) = catalogWork { scope -> api.perform("v1/projects/conversations/$id","DELETE"); if (scope == generation) mutable.update { it.copy(conversations = it.conversations.filterNot { c -> c.id == id }) } }
    fun newSearch() { val old = state.value; mutable.update { it.copy(criteria = ProjectCriteria(city = old.criteria.city), conversationId = null, turns = emptyList(), pending = null, results = emptyList(), selection = emptySet(), tab = 0, paths = it.paths.mapIndexed { index,path -> when(index){0->listOf(CatalogRoute(CatalogPage.SEARCH));2->listOf(CatalogRoute(CatalogPage.PROFILE));else->path} }) }; work { withContext(Dispatchers.IO) { repo.files.delete(record("pending")) } } }
    fun send(message: String, retry: Boolean = false) {
        if (state.value.busy || !state.value.supported || state.value.offline || (!retry && state.value.pending != null)) return
        val text = message.trim(); if (!retry && (text.isEmpty() || text.codePointCount(0,text.length)>2000)) return
        work(true) { scope ->
            var request = if(retry) state.value.pending ?: return@work else PendingProjectMessage(UUID.randomUUID().toString(),state.value.conversationId,UUID.randomUUID().toString(),text,state.value.criteria)
            suspend fun persist() { val raw = API_JSON.encodeToString(request); withContext(Dispatchers.IO) { repo.files.write(record("pending"),raw) }; if(scope==generation) mutable.update { it.copy(pending = request) } }
            persist()
            if (request.conversationId == null) {
                val created = api.call<ProjectConversation>("v1/projects/conversations","POST",API_JSON.encodeToString(CreateConversation(request.criteria,request.clientConversationId)))
                if(scope!=generation) return@work
                request = request.copy(conversationId = created.id); persist()
            }
            val reply = api.call<ProjectTurnResponse>("v1/projects/conversations/${request.conversationId}/turns","POST",API_JSON.encodeToString(SendTurn(request.clientTurnId,request.message,request.criteria)))
            if(scope!=generation) return@work
            withContext(Dispatchers.IO) { repo.files.delete(record("pending")) }
            val history = api.call<ProjectConversationHistory>("v1/projects/conversations/${reply.conversationId}")
            if(scope!=generation) return@work
            mutable.update { it.copy(pending = null, conversationId = reply.conversationId, criteria = reply.criteria, turns = history.turns, historyCursor = history.nextBefore, results = reply.results?.items ?: it.results, suggestions = reply.suggestions) }
            if(state.value.page!=CatalogPage.CONVERSATION)push(CatalogPage.CONVERSATION)
            history()
        }
    }
    fun sources() = catalogWork { scope -> val items = api.call<ProjectItems<CatalogSource>>("v1/catalog/sources").items; if(scope==generation)mutable.update{it.copy(sources=items)} }
    fun report(id: String, project: String, category: String, message: String, onResult: (Boolean)->Unit) {
        if (!state.value.supported || state.value.offline) { onResult(false); return }
        catalogWork(true) { scope -> val receipt=api.call<ReportReceipt>("v1/data-reports","POST",API_JSON.encodeToString(ReportBody(id,project,category,message))); if(scope==generation)onResult(receipt.status=="received") }
    }
    companion object { fun factory(repo:MekenRepository):ViewModelProvider.Factory=object:ViewModelProvider.Factory { @Suppress("UNCHECKED_CAST") override fun <T:ViewModel> create(modelClass:Class<T>):T=CatalogViewModel(repo) as T } }
}
internal fun millions(amount:Long):String=NumberFormat.getNumberInstance(Locale.forLanguageTag("ru-KZ")).apply { maximumFractionDigits=2 }.format(amount/1_000_000.0)
internal val ProjectPrice.label:String get()=amountKzt?.takeIf { kind!="unknown" }?.let { (if(kind=="published_starting_price")"от " else "Лоты от ")+millions(it)+" млн ₸" } ?: "Цена не опубликована"
internal val CatalogProject.isDemo:Boolean get()=provenance=="demo"
internal val CatalogProject.sourceLink:String? get()=if(isDemo)null else safeHttpsUrl(websiteUrl ?: sourceUrl)
internal val CatalogProject.sourceLabel:String get()="${if(isDemo)"Демо · " else ""}$providerName · ${dateLabel(observedAt)}"
internal val CatalogProject.stageLabel:String get()=when(stage){"commissioned"->"Сдан";"under_construction"->"Строится";"planned"->"Планируется";else->"Стадия не опубликована"}
internal fun dateLabel(raw:String):String=raw.take(10).split('-').let { if(it.size==3)"${it[2]}.${it[1]}.${it[0]}" else "Дата не указана" }
internal val ProjectCriteria.priceSummary:String get()=priceMax?.let{"До ${millions(it)} млн ₸"} ?: "Не выбрана"
internal val ProjectCriteria.priceModeLabel:String get()=if(priceMode=="published_starting_price")"Цена ЖК «от»" else "Минимум по опубликованным лотам"
internal val ProjectLayout.parameterLabel:String get()="${rooms?.let{"$it комнаты"} ?: "Комнаты не опубликованы"} · ${areaM2?.let{NumberFormat.getNumberInstance(Locale.forLanguageTag("ru-KZ")).format(it)+" м²"} ?: "Площадь не опубликована"}"
