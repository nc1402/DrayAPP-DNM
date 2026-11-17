package vn.anphat.dnm

import android.os.Bundle
import android.util.Base64
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URI
import java.net.URLEncoder
import java.nio.charset.StandardCharsets

private val Blue = Color(0xFF1F6FD6)
private val Red = Color(0xFFE2231A)
private val Green = Color(0xFF16825D)
private const val D = "Device.DeviceInfo."
private const val LAN_CLIENTS = "Device.X_001DAA.Monitoring.Clients_List.Clients_List."
private const val WIFI_CLIENTS = "Device.X_001DAA.System_Maintenance.Management.Wireless_Clients_Overview."
private fun JSONArray.objects(): List<JSONObject> = (0 until length()).mapNotNull { optJSONObject(it) }
private fun JSONObject.str(key: String, fallback: String="—"): String = opt(key)?.takeUnless { it == JSONObject.NULL }?.toString()?.takeIf { it.isNotBlank() } ?: fallback
private fun JSONObject.model() = optJSONObject("info")?.str("model", "")?.takeIf { it.isNotBlank() } ?: str("name")
private fun JSONObject.isAp() = model().replace(" ", "").contains("VigorAP", true) || str("name").replace(" ", "").contains("VigorAP", true)
private fun JSONObject.isRouter() = !isAp() && model().startsWith("Vigor", true)
private fun JSONObject.statusLabel() = when(str("status")) { "online" -> "Online"; "offline" -> "Offline"; else -> "Chưa quét" }
private fun JSONObject.statusColor() = when(str("status")) { "online" -> Green; "offline" -> Red; else -> Color(0xFFB08032) }
private fun prettyUp(value: String): String { val s=value.toLongOrNull() ?: return "—"; return "${s/86400}d ${(s%86400)/3600}h ${(s%3600)/60}m" }
private fun flat(result: JSONObject): Map<String,String> = buildMap { for(o in result.optJSONArray("objects")?.objects().orEmpty()) { val p=o.optJSONObject("params") ?: continue; for(k in p.keys()) put(o.str("path", "")+k,p.str(k)) } }

private class Controller(private val fixture: JSONObject, private val schema: String, private val url: String? = null, private val password: String = "") {
    val localDemo get()=url==null
    private fun endpoint(): URI {
        val u=URI((url ?: error("Demo")).trim().trimEnd('/'))
        require(u.scheme in listOf("http", "https") && u.host != null && u.userInfo == null && u.rawQuery == null && u.fragment == null && (u.path.isNullOrEmpty() || u.path=="/")) { "Nhập URL Controller, ví dụ http://192.168.9.100:8080" }
        if(u.scheme=="http") {
            val oct=u.host.split('.').mapNotNull { it.toIntOrNull() }
            val lan=oct.size==4 && oct.all{it in 0..255} && (oct[0]==10 || oct[0]==127 || (oct[0]==192 && oct[1]==168) || (oct[0]==172 && oct[1] in 16..31))
            require(lan) { "HTTP chỉ dùng cho IPv4 LAN thử nghiệm. Kết nối khác cần HTTPS." }
        }
        return u
    }
    private suspend fun request(path:String, body:JSONObject?=null): String = withContext(Dispatchers.IO) {
        val root=endpoint(); val c=URI(root.toString().trimEnd('/')+path).toURL().openConnection() as HttpURLConnection
        try {
            c.connectTimeout=8000; c.readTimeout=65000; c.instanceFollowRedirects=false
            c.setRequestProperty("Accept","application/json")
            if(password.isNotEmpty()) c.setRequestProperty("Authorization","Basic "+Base64.encodeToString("dnm:$password".toByteArray(StandardCharsets.UTF_8),Base64.NO_WRAP))
            if(body!=null) { c.requestMethod="POST"; c.doOutput=true; c.setRequestProperty("Content-Type","application/json; charset=utf-8"); c.setRequestProperty("X-USP-Web","1"); c.outputStream.use{it.write(body.toString().toByteArray(StandardCharsets.UTF_8))} }
            val code=c.responseCode
            val response=(if(code in 200..299) c.inputStream else c.errorStream)?.bufferedReader(StandardCharsets.UTF_8)?.use{it.readText()}.orEmpty()
            if(code !in 200..299) error(if(code==401) "Sai hoặc thiếu mật khẩu Controller" else "HTTP $code: ${response.take(400)}")
            response
        } finally { c.disconnect() }
    }
    suspend fun state()=if(localDemo) fixture.getJSONObject("state") else JSONObject(request("/api/state"))
    suspend fun devices(scan:Boolean=false)=if(localDemo) fixture.getJSONArray("devices").objects() else JSONObject(request(if(scan) "/api/devices/scan" else "/api/devices",if(scan) JSONObject() else null)).getJSONArray("devices").objects()
    suspend fun pages()=if(localDemo) fixture.getJSONArray("pages").objects() else JSONArray(request("/api/pages")).objects()
    suspend fun read(id:String,path:String): JSONObject {
        require(path.startsWith("Device.")) { "Path phải bắt đầu bằng Device." }
        val result=if(localDemo) fixture.getJSONObject("data").optJSONObject(id)?.optJSONObject(path) ?: error("Demo offline chưa có dữ liệu cho path này") else JSONObject(request("/api/get",JSONObject().put("device",id).put("paths",JSONArray().put(path)))).getJSONArray("results").getJSONObject(0)
        result.optJSONObject("error")?.let { error("USP ${it.str("code")}: ${it.str("message")}") }
        return result
    }
    suspend fun search(q:String): List<List<String>> = if(localDemo) withContext(Dispatchers.Default) { val tokens=q.lowercase().split(Regex("\\s+")).filter{it.isNotBlank()}; schema.lineSequence().map{it.trim().split(Regex("\\s+"))}.filter{it.size>=3 && it[0].startsWith("Device.") && tokens.all { t->it[0].lowercase().contains(t) }}.take(150).map{it.take(3)}.toList() } else {
        val a=JSONObject(request("/api/schema/search?q="+URLEncoder.encode(q,"UTF-8"))).getJSONArray("results"); (0 until a.length()).map { val r=a.getJSONArray(it); (0 until r.length()).map{j->r.getString(j)} }
    }
    suspend fun add(id:String,name:String,topic:String) { require(!localDemo){"Chỉ thêm thiết bị trong chế độ Controller"}; val r=JSONObject(request("/api/device/save",JSONObject().put("id",id).put("name",name).put("topic",topic))); check(r.optBoolean("ok")){"Không thêm được thiết bị"} }
}

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) { super.onCreate(savedInstanceState); setContent { val dark=isSystemInDarkTheme(); MaterialTheme(colorScheme=if(dark) darkColorScheme(primary=Color(0xFF8BB9FF),secondary=Color(0xFFAAC7EB)) else lightColorScheme(primary=Blue,secondary=Color(0xFF527096),background=Color(0xFFF1F5FB),surface=Color.White)) { DnmApp() } } }
}
private data class Tab(val name:String,val icon:ImageVector)
private val tabs=listOf(Tab("Tổng quan",Icons.Default.Dashboard),Tab("Thiết bị",Icons.Default.Router),Tab("Clients",Icons.Default.Groups),Tab("Cấu hình",Icons.Default.Settings),Tab("Thêm",Icons.Default.MoreHoriz))

@OptIn(ExperimentalMaterial3Api::class)
@Composable private fun DnmApp() {
    val context=LocalContext.current
    val fixture=remember{JSONObject(context.assets.open("demo.json").bufferedReader().use{it.readText()})}
    val schema=remember{context.assets.open("ap905.schema").bufferedReader().use{it.readText()}}
    val offline=remember{Controller(fixture,schema)}
    var api by remember{mutableStateOf(offline)}
    var state by remember{mutableStateOf(fixture.getJSONObject("state"))}
    var devices by remember{mutableStateOf(fixture.getJSONArray("devices").objects())}
    var pages by remember{mutableStateOf(fixture.getJSONArray("pages").objects())}
    var tab by remember{mutableIntStateOf(0)}
    var current by remember{mutableStateOf<String?>(null)}
    var section by remember{mutableStateOf("")}
    var sectionTitle by remember{mutableStateOf("")}
    var detail by remember{mutableStateOf<JSONObject?>(null)}
    var data by remember{mutableStateOf<JSONObject?>(null)}
    var clientId by remember{mutableStateOf("")}
    var clients by remember{mutableStateOf<JSONObject?>(null)}
    var query by remember{mutableStateOf("")}
    var filter by remember{mutableStateOf("Tất cả")}
    var picked by remember{mutableStateOf(setOf<String>())}
    var ssid by remember{mutableStateOf("ANPHAT-Office")}
    var preview by remember{mutableStateOf(false)}
    var busy by remember{mutableStateOf(false)}
    var errorText by remember{mutableStateOf("")}
    var server by remember{mutableStateOf("http://192.168.9.100:8080")}
    var pw by remember{mutableStateOf("")}
    var showAdd by remember{mutableStateOf(false)}
    var agentId by remember{mutableStateOf("")}
    var agentName by remember{mutableStateOf("")}
    var agentTopic by remember{mutableStateOf("")}
    var explorer by remember{mutableStateOf(false)}
    var pathQuery by remember{mutableStateOf("SSID")}
    var results by remember{mutableStateOf(listOf<List<String>>())}
    var pathRead by remember{mutableStateOf(D)}
    var explorerData by remember{mutableStateOf<JSONObject?>(null)}
    val scope=rememberCoroutineScope()
    fun task(block:suspend ()->Unit){ if(busy)return; busy=true; errorText=""; scope.launch{ try{block()}catch(e:Exception){errorText=e.message?:"Lỗi kết nối"}finally{busy=false} } }
    fun resetSelection(){current=null;section="";detail=null;data=null;clients=null;clientId="";picked=emptySet();preview=false;results=emptyList();explorerData=null}
    fun refresh(){task{state=api.state();devices=api.devices(true)}}
    fun openDevice(id:String){current=id;section="";detail=null;data=null;tab=1;task{detail=api.read(id,D)}}
    fun navigate(i:Int){if(busy)return;tab=i;current=null;section="";explorer=false;errorText=""}
    val selected=devices.find{it.str("id")==current}
    val isDemo=api.localDemo||state.optBoolean("demo")
    val title=when { tab==1 && section.isNotBlank()->sectionTitle;tab==1&&selected!=null->selected.model();tab==4&&explorer->"TR-369 Explorer";else->tabs[tab].name }
    val on=devices.count{it.str("status")=="online"}
    val off=devices.count{it.str("status")=="offline"}
    Scaffold(topBar={TopAppBar(title={Column{Text("DrayTek  /  NETWORK MANAGER",color=Red,fontSize=11.sp,fontWeight=FontWeight.Bold);Text(title,fontWeight=FontWeight.Bold)}},navigationIcon={if((tab==1&&current!=null)||(tab==4&&explorer))IconButton(onClick={if(busy)return@IconButton;if(explorer)explorer=false else if(section.isNotBlank()){section="";data=null}else current=null}){Icon(Icons.Default.ArrowBack,"Quay lại")}},actions={IconButton(onClick={refresh()},enabled=!busy){Icon(Icons.Default.Refresh,"Quét trạng thái")}})},bottomBar={NavigationBar{tabs.forEachIndexed{i,t->NavigationBarItem(selected=tab==i,onClick={navigate(i)},enabled=!busy,icon={Icon(t.icon,null)},label={Text(t.name,fontSize=10.sp)})}}}){padding->
        Column(Modifier.fillMaxSize().padding(padding).verticalScroll(rememberScrollState()).padding(16.dp),verticalArrangement=Arrangement.spacedBy(12.dp)) {
            Surface(color=if(isDemo) MaterialTheme.colorScheme.tertiaryContainer else MaterialTheme.colorScheme.secondaryContainer,shape=RoundedCornerShape(12.dp)){Text(if(api.localDemo)"DEMO OFFLINE • Không gửi lệnh đến router" else if(isDemo)"CONTROLLER DEMO • Dữ liệu giả lập" else "CONTROLLER • ${state.str("status")}",Modifier.fillMaxWidth().padding(12.dp),fontSize=12.sp,fontWeight=FontWeight.SemiBold)}
            if(busy) LinearProgressIndicator(Modifier.fillMaxWidth())
            if(errorText.isNotBlank()) Panel{Text(errorText,color=MaterialTheme.colorScheme.error);TextButton(onClick={errorText=""}){Text("Đóng")}}
            when(tab){
                0->{
                    Card(colors=CardDefaults.cardColors(containerColor=Blue,contentColor=Color.White),shape=RoundedCornerShape(20.dp)){Column(Modifier.fillMaxWidth().padding(20.dp)){Text("NETWORK HEALTH",fontSize=12.sp);Text("$on / ${devices.size}",fontSize=40.sp,fontWeight=FontWeight.Bold);Text("Thiết bị online  •  $off offline");Text("${devices.size-on-off} chưa quét",fontSize=12.sp)}}
                    Row(horizontalArrangement=Arrangement.spacedBy(10.dp)){Metric("Router",devices.count{it.isRouter()}.toString(),Modifier.weight(1f));Metric("Access Point",devices.count{it.isAp()}.toString(),Modifier.weight(1f))}
                    Row(horizontalArrangement=Arrangement.spacedBy(10.dp)){Metric("Chưa phân loại",devices.count{!it.isAp()&&!it.isRouter()}.toString(),Modifier.weight(1f));Metric("Offline",off.toString(),Modifier.weight(1f))}
                    Panel{Text("Controller",fontWeight=FontWeight.Bold);Text(state.str("status"));Text("Schema: ${state.optJSONObject("schema")?.str("count")?:"—"} khai báo");Button(onClick={refresh()},enabled=!busy,modifier=Modifier.fillMaxWidth()){Text("Quét tất cả thiết bị")}}
                    Text("Thiết bị",fontWeight=FontWeight.Bold,fontSize=18.sp)
                    devices.take(5).forEach{DeviceCard(it,enabled=!busy,onClick={openDevice(it.str("id"))})}
                    Text("Traffic và topology lịch sử sẽ bổ sung khi có nguồn dữ liệu xác thực.",fontSize=12.sp,color=MaterialTheme.colorScheme.onSurfaceVariant)
                }
                1->{if(selected==null){
                    OutlinedTextField(query,{query=it},label={Text("Tìm tên, model, Endpoint ID")},singleLine=true,modifier=Modifier.fillMaxWidth(),leadingIcon={Icon(Icons.Default.Search,null)})
                    Row(horizontalArrangement=Arrangement.spacedBy(5.dp)){listOf("Tất cả","Router","AP","Offline").forEach{f->FilterChip(selected=filter==f,onClick={filter=f},label={Text(f,fontSize=11.sp)})}}
                    val list=devices.filter{d->(filter=="Tất cả"||filter=="Router"&&d.isRouter()||filter=="AP"&&d.isAp()||filter=="Offline"&&d.str("status")=="offline")&&("${d.str("name")} ${d.model()} ${d.str("id")}".contains(query,true))}
                    Text("${list.size} thiết bị",fontWeight=FontWeight.Bold)
                    list.forEach{d->DeviceCard(d,enabled=!busy,onClick={openDevice(d.str("id"))})}
                    OutlinedButton(onClick={showAdd=true},enabled=!busy&&!api.localDemo,modifier=Modifier.fillMaxWidth()){Text("+ Thêm thiết bị")}
                    Button(onClick={navigate(3)},enabled=!busy,modifier=Modifier.fillMaxWidth()){Text("Chọn nhiều thiết bị")}
                }else if(section.isNotBlank()){
                    Text(section,fontSize=11.sp,color=MaterialTheme.colorScheme.onSurfaceVariant)
                    OutlinedButton(onClick={task{data=api.read(selected.str("id"),section)}},enabled=!busy){Text("Đọc lại")}
                    data?.let{ObjectView(it)} ?: Text("Chưa có dữ liệu.")
                }else{
                    DeviceCard(selected,enabled=false,onClick={})
                    val info=selected.optJSONObject("info")?:JSONObject();val f=detail?.let{flat(it)}?:emptyMap()
                    Row(horizontalArrangement=Arrangement.spacedBy(10.dp)){Metric("CPU",(f[D+"ProcessStatus.CPUUsage"]?:info.str("cpu")).let{if(it=="—")it else "$it%"},Modifier.weight(1f));Metric("RAM",(f[D+"MemoryStatus.MemoryMonitor.MemUtilization"]?:info.str("ram")).let{if(it=="—")it else "$it%"},Modifier.weight(1f))}
                    Panel{Text("Thông tin hệ thống",fontWeight=FontWeight.Bold);KV("Firmware",f[D+"SoftwareVersion"]?:info.str("firmware"));KV("Serial",f[D+"SerialNumber"]?:info.str("serial"));KV("Uptime",prettyUp(f[D+"UpTime"]?:info.str("uptime")));KV("Endpoint ID",selected.str("id"));OutlinedButton(onClick={task{detail=api.read(selected.str("id"),D)}},enabled=!busy){Text("Đọc lại thiết bị")}}
                    Text("Thông tin & Data Model",fontWeight=FontWeight.Bold)
                    for(p in pages.filter{it.str("type")=="sections" && !(selected.isAp()&&it.str("id")=="wan")}){Panel{Text(p.str("title"),fontWeight=FontWeight.Bold);p.optJSONArray("sections")?.objects().orEmpty().forEach{s->ListItem(headlineContent={Text(s.str("title"))},supportingContent={Text("GET • Kiểm tra hỗ trợ khi đọc",fontSize=11.sp)},trailingContent={Icon(Icons.Default.ChevronRight,null)},modifier=Modifier.clickable(enabled=!busy){section=s.str("base");sectionTitle=s.str("title");data=null;task{data=api.read(selected.str("id"),section)}})}}}
                }}
                2->{Text("Clients theo thiết bị",fontWeight=FontWeight.Bold,fontSize=18.sp);Text("Đọc dữ liệu riêng từng Agent; không cộng trùng client giữa các thiết bị.",fontSize=12.sp)
                    devices.forEach{d->OutlinedButton(onClick={clientId=d.str("id");clients=null;task{clients=api.read(clientId,if(d.isAp())WIFI_CLIENTS else LAN_CLIENTS)}},enabled=!busy,modifier=Modifier.fillMaxWidth()){Text(d.model())}}
                    if(clientId.isNotEmpty())Text("Agent: $clientId",fontSize=12.sp);clients?.let{ObjectView(it)}
                }
                3->{Text("Chọn thiết bị",fontWeight=FontWeight.Bold,fontSize=18.sp);Text("${picked.size} đã chọn • Chỉ chuẩn bị Profile, chưa ghi cấu hình.")
                    devices.forEach{d->Panel{Row(verticalAlignment=Alignment.CenterVertically){Checkbox(checked=d.str("id") in picked,onCheckedChange={checked->picked=if(checked)picked+d.str("id") else picked-d.str("id");preview=false},enabled=!busy&&d.isAp()&&d.str("status")=="online");Column(Modifier.weight(1f)){Text(d.model(),fontWeight=FontWeight.Medium);Text(d.statusLabel(),color=d.statusColor(),fontSize=12.sp)}}}}
                    Panel{Text("Wi-Fi Profile",fontWeight=FontWeight.Bold);OutlinedTextField(ssid,{ssid=it;preview=false},label={Text("SSID dự kiến")},singleLine=true,modifier=Modifier.fillMaxWidth());Button(onClick={preview=true},enabled=picked.isNotEmpty()&&ssid.isNotBlank(),modifier=Modifier.fillMaxWidth()){Text("Xem trước (${picked.size})")};if(preview){Text("SSID: $ssid",fontWeight=FontWeight.Bold);devices.filter{it.str("id") in picked}.forEach{Text("• ${it.model()}")};Text("Không có lệnh SET được gửi. Kiểm tra mapping và quyền ghi ở giai đoạn 4.",color=MaterialTheme.colorScheme.onSurfaceVariant)}}
                }
                4->{if(!explorer){
                    Panel{Text("USP Controller",fontWeight=FontWeight.Bold,fontSize=18.sp);OutlinedTextField(server,{server=it},label={Text("URL Controller")},singleLine=true,modifier=Modifier.fillMaxWidth());OutlinedTextField(pw,{pw=it},label={Text("Mật khẩu web Controller")},singleLine=true,visualTransformation=PasswordVisualTransformation(),modifier=Modifier.fillMaxWidth());Text("HTTP chỉ dành cho LAN test. Dùng HTTPS + VPN cho triển khai thực tế. Mật khẩu không lưu vào thiết bị.",fontSize=12.sp);Button(onClick={task{val newApi=Controller(fixture,schema,server,pw);val s=newApi.state();val ds=newApi.devices();val ps=newApi.pages();api=newApi;state=s;devices=ds;pages=ps;resetSelection();tab=0}},enabled=!busy,modifier=Modifier.fillMaxWidth()){Text("Kết nối Controller")};OutlinedButton(onClick={api=offline;state=fixture.getJSONObject("state");devices=fixture.getJSONArray("devices").objects();pages=fixture.getJSONArray("pages").objects();pw="";resetSelection();tab=0},enabled=!busy,modifier=Modifier.fillMaxWidth()){Text("Demo offline")}}
                    Panel{Text("Quản trị",fontWeight=FontWeight.Bold);OutlinedButton(onClick={explorer=true},enabled=!busy,modifier=Modifier.fillMaxWidth()){Text("TR-369 Explorer (GET)")};OutlinedButton(onClick={showAdd=true},enabled=!busy&&!api.localDemo,modifier=Modifier.fillMaxWidth()){Text("Thêm Agent Endpoint ID")}}
                    Text("DNM v0.1.0 • Android • TR-369 / USP 1.3",fontSize=12.sp)
                }else{
                    val fallback=devices.firstOrNull()?.str("id")?:"";val target=clientId.takeIf{v->devices.any{it.str("id")==v}}?:fallback
                    Text("Chọn Agent đọc dữ liệu",fontWeight=FontWeight.Bold);devices.forEach{d->FilterChip(selected=target==d.str("id"),onClick={clientId=d.str("id");explorerData=null},label={Text(d.model())})}
                    OutlinedTextField(pathQuery,{pathQuery=it},label={Text("Tìm parameter")},modifier=Modifier.fillMaxWidth());Button(onClick={task{results=api.search(pathQuery)}},enabled=!busy&&pathQuery.isNotBlank()){Text("Tìm schema")}
                    Text("Schema có thể chứa path không được model hỗ trợ. Thay {i} bằng instance thực trước khi GET.",fontSize=12.sp)
                    results.forEach{r->Panel{Text(r.getOrElse(0){""},fontSize=12.sp);Text(r.drop(1).joinToString(" • "),fontSize=11.sp);TextButton(onClick={pathRead=r[0];explorerData=null},enabled=!busy){Text("Chọn path")}}}
                    OutlinedTextField(pathRead,{pathRead=it;explorerData=null},label={Text("Path GET")},modifier=Modifier.fillMaxWidth());Button(onClick={task{explorerData=api.read(target,pathRead.trim())}},enabled=!busy&&target.isNotBlank()&&pathRead.startsWith("Device.")&&!pathRead.contains("{i}")){Text("GET")};explorerData?.let{ObjectView(it)}
                }}
            }
            Spacer(Modifier.height(8.dp))
        }
    }
    if(showAdd) AlertDialog(onDismissRequest={if(!busy)showAdd=false},title={Text("Thêm USP Agent")},text={Column(verticalArrangement=Arrangement.spacedBy(8.dp)){OutlinedTextField(agentId,{agentId=it},label={Text("Agent Endpoint ID")});OutlinedTextField(agentName,{agentName=it},label={Text("Tên thiết bị")});OutlinedTextField(agentTopic,{agentTopic=it},label={Text("MQTT topic (tùy chọn)")})}},confirmButton={TextButton(enabled=!busy&&agentId.isNotBlank(),onClick={task{api.add(agentId.trim(),agentName.trim(),agentTopic.trim());devices=api.devices();showAdd=false;agentId="";agentName="";agentTopic=""}}){Text("Lưu")}},dismissButton={TextButton(onClick={showAdd=false},enabled=!busy){Text("Hủy")}})
}
@Composable private fun Panel(content:@Composable ColumnScope.()->Unit){Card(shape=RoundedCornerShape(16.dp),colors=CardDefaults.cardColors(containerColor=MaterialTheme.colorScheme.surface)){Column(Modifier.fillMaxWidth().padding(14.dp),verticalArrangement=Arrangement.spacedBy(8.dp),content=content)}}
@Composable private fun Metric(label:String,value:String,modifier:Modifier=Modifier){Card(modifier,shape=RoundedCornerShape(16.dp),colors=CardDefaults.cardColors(containerColor=MaterialTheme.colorScheme.surface)){Column(Modifier.padding(16.dp)){Text(label,fontSize=13.sp,color=MaterialTheme.colorScheme.onSurfaceVariant);Text(value,fontWeight=FontWeight.Bold,fontSize=27.sp)}}}
@Composable private fun DeviceCard(d:JSONObject,enabled:Boolean,onClick:()->Unit){Panel{Row(Modifier.fillMaxWidth().clickable(enabled=enabled,onClick=onClick),verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(12.dp)){Surface(shape=RoundedCornerShape(12.dp),color=MaterialTheme.colorScheme.secondaryContainer){Icon(if(d.isAp())Icons.Default.Wifi else Icons.Default.Router,null,Modifier.padding(12.dp).size(28.dp),tint=MaterialTheme.colorScheme.primary)};Column(Modifier.weight(1f)){Text(d.str("name"),fontWeight=FontWeight.Bold);Text(d.model(),fontSize=12.sp);Text(d.str("id"),fontSize=11.sp,color=MaterialTheme.colorScheme.onSurfaceVariant)};Text("● ${d.statusLabel()}",color=d.statusColor(),fontSize=11.sp);if(enabled)Icon(Icons.Default.ChevronRight,null)}}}
@Composable private fun KV(k:String,v:String){Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.spacedBy(8.dp)){Text(k,Modifier.weight(1f),fontSize=12.sp,color=MaterialTheme.colorScheme.onSurfaceVariant);Text(v,Modifier.weight(1.4f),fontSize=12.sp,fontWeight=FontWeight.Medium)}}
@Composable private fun ObjectView(result:JSONObject){val objects=result.optJSONArray("objects")?.objects().orEmpty();Text("${objects.size} object",fontSize=12.sp);for(o in objects.take(100)){Panel{Text(o.str("path"),fontSize=11.sp,fontWeight=FontWeight.Bold);val params=o.optJSONObject("params")?:JSONObject();for(k in params.keys().asSequence().take(100)){val sensitive=Regex("password|passphrase|preshared|secret|(^|_)psk|private.?key",RegexOption.IGNORE_CASE).containsMatchIn(k);KV(k,if(sensitive)"••••••" else params.str(k))}}};if(objects.size>100)Text("Hiển thị 100 object đầu tiên.",fontSize=12.sp)}
