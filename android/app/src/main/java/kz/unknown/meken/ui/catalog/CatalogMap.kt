package kz.unknown.meken.ui.catalog

import android.annotation.SuppressLint
import android.webkit.*
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.*
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import kotlinx.serialization.json.*
import kz.unknown.meken.core.API_JSON
import kz.unknown.meken.core.catalog.*
import kz.unknown.meken.ui.openHttps

private class MapBridge(private val ids:Set<String>,private val post:((()->Unit)->Unit),private val selected:(String)->Unit,private val bounds:(ProjectBounds)->Unit){
    @JavascriptInterface fun select(id:String){if(id in ids)post{selected(id)}}
    @JavascriptInterface fun area(south:Double,west:Double,north:Double,east:Double){if(listOf(south,west,north,east).all{it.isFinite()}&&south>=-90&&north<=90&&west>=-180&&east<=180&&south<=north&&west<=east)post{bounds(ProjectBounds(south=south,west=west,north=north,east=east))}}
}
@SuppressLint("SetJavaScriptEnabled")
@Composable internal fun MapScreen(state:CatalogState,model:CatalogViewModel){
    val context=LocalContext.current
    var selected by rememberSaveable{mutableStateOf<String?>(null)};var area by remember{mutableStateOf<ProjectBounds?>(state.bounds)}
    val points=state.mapped.filter{it.latitude!=null&&it.longitude!=null}
    val html=remember(points,state.bounds){
        val js=context.assets.open("catalog-map/leaflet.js").bufferedReader().use{it.readText()};val css=context.assets.open("catalog-map/leaflet.css").bufferedReader().use{it.readText()}
        val values=buildJsonArray{points.forEach{p->add(buildJsonObject{put("id",p.id);put("name",p.name);put("latitude",p.latitude!!);put("longitude",p.longitude!!);put("price",p.displayPrice.amountKzt?.let(::millions) ?: "Нет цены")})}}.toString().replace("<","\\u003c").replace(">","\\u003e")
        val bounds=state.bounds?.let{ "[[${it.south},${it.west}],[${it.north},${it.east}]]" }
        """<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src https://tile.openstreetmap.org data:; connect-src 'none'"><style>${css}html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#eef2ef}#map{position:fixed;inset:0;width:100vw;height:100vh;background:#eef2ef}.price{background:#20564c;color:white;border-radius:30px;padding:9px;font:600 12px system-ui;white-space:nowrap;text-align:center}.leaflet-control-attribution{font-size:10px}</style></head><body><div id="map"></div><script>${js}</script><script>const container=document.getElementById('map');function sizeMap(){container.style.width=window.innerWidth+'px';container.style.height=window.innerHeight+'px'}sizeMap();const map=L.map('map',{zoomControl:false});window.addEventListener('resize',()=>{sizeMap();map.invalidateSize()});L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>'}).addTo(map);const points=${values};const markers=[];for(const p of points){let el=document.createElement('div');el.className='price';el.textContent=p.price;let marker=L.marker([p.latitude,p.longitude],{icon:L.divIcon({html:el,iconSize:[64,34],className:''})}).addTo(map);marker.on('click',()=>MekenMap.select(p.id));markers.push(marker)}if(${bounds ?: "null"})map.fitBounds(${bounds ?: "null"});else if(markers.length)map.fitBounds(L.featureGroup(markers).getBounds().pad(.2),{maxZoom:14});else map.setView([0,0],2);function area(){const b=map.getBounds();MekenMap.area(b.getSouth(),b.getWest(),b.getNorth(),b.getEast())}map.on('moveend',area);area();</script></body></html>"""
    }
    Column(verticalArrangement=Arrangement.spacedBy(9.dp)){
        Header(state.criteria.city ?: "Карта ЖК"){TextButton(onClick=model::back){Text("Список",style=MaterialTheme.typography.labelSmall)}}
        Box(Modifier.padding(horizontal=14.dp)){CriteriaChips(state,model)}
        Row(Modifier.padding(horizontal=14.dp),verticalAlignment=Alignment.CenterVertically){TextButton(onClick=model::back){Text("Список")};Text("Карта",style=MaterialTheme.typography.labelSmall);Spacer(Modifier.weight(1f));TextButton(onClick={model.map(area)}){Text("Искать в этой области",style=MaterialTheme.typography.labelSmall)}}
        Box(Modifier.weight(1f).fillMaxWidth()){
            key(html){AndroidView(factory={ctx->WebView(ctx).apply{
                settings.javaScriptEnabled=true;settings.allowFileAccess=false;settings.allowContentAccess=false;settings.domStorageEnabled=false;settings.setGeolocationEnabled(false);settings.mixedContentMode=WebSettings.MIXED_CONTENT_NEVER_ALLOW;settings.cacheMode=WebSettings.LOAD_DEFAULT;settings.userAgentString=settings.userAgentString+" Meken/1.0 (kz.unknown.meken)"
                CookieManager.getInstance().setAcceptThirdPartyCookies(this,false)
                webChromeClient=object:WebChromeClient(){override fun onConsoleMessage(message:ConsoleMessage):Boolean{if(kz.unknown.meken.BuildConfig.DEBUG)android.util.Log.d("MekenMap",message.message());return true}}
                addJavascriptInterface(MapBridge(points.map{it.id}.toSet(),{action->post{action()}},{selected=it},{area=it}),"MekenMap")
                webViewClient=object:WebViewClient(){override fun onPageFinished(view:WebView,url:String){view.evaluateJavascript("setTimeout(function(){sizeMap();map.invalidateSize();console.log(\"map-state\",window.innerHeight,document.getElementById(\"map\").clientHeight,typeof L);},50);",null)};override fun shouldOverrideUrlLoading(view:WebView,request:WebResourceRequest):Boolean{openHttps(context,request.url.toString());return true}}
                loadDataWithBaseURL("https://meken-map.invalid/",html,"text/html","UTF-8",null)
            }},onReset=null,onRelease={it.removeJavascriptInterface("MekenMap");it.destroy()},modifier=Modifier.fillMaxSize())}
            if(points.isEmpty())Surface(Modifier.align(Alignment.Center).padding(20.dp),shape=androidx.compose.foundation.shape.RoundedCornerShape(9.dp)){Text(if(state.error==null)"Нет опубликованных координат для показа на карте" else "Не удалось загрузить расположение ЖК",Modifier.padding(16.dp),style=MaterialTheme.typography.bodySmall)}
        }
        if(state.unknownCoordinates>0)Text("Без координат: ${state.unknownCoordinates} ЖК. Они доступны в списке.",style=MaterialTheme.typography.labelSmall,modifier=Modifier.padding(horizontal=14.dp))
        (state.mapped.firstOrNull{it.id==selected} ?: state.mapped.firstOrNull())?.let{p->
            Row(Modifier.padding(horizontal=14.dp),verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(10.dp)){Artwork(p,Modifier.width(85.dp).height(65.dp));Column(Modifier.weight(1f)){Text(p.name,style=MaterialTheme.typography.titleSmall);Text(p.sourceLabel,style=MaterialTheme.typography.labelSmall);Text(p.displayPrice.label,style=MaterialTheme.typography.labelMedium)}}
            Box(Modifier.padding(horizontal=14.dp,vertical=8.dp)){Primary("Открыть ЖК"){model.project(p)}}
        }
    }
}
