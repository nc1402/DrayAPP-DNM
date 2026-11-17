#!/usr/bin/env python3
"""
USP / TR-369 Web Controller (DrayTek)  -  chỉ cần: pip install paho-mqtt protobuf
Cần đặt cùng thư mục: usp_msg_1_3_pb2.py, usp_record_1_3_pb2.py

Chạy:
    python usp_web.py                  # mở http://127.0.0.1:8080
    python usp_web.py --demo           # xem thử giao diện với dữ liệu giả, không cần router
    python usp_web.py --host 0.0.0.0 --password MAT_KHAU   # cho máy khác trong mạng truy cập
"""
import argparse
import base64
import glob
import hmac
import json
import os
import random
import re
import threading
import time
import webbrowser
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

try:
    import paho.mqtt.client as mqtt
    import usp_msg_1_3_pb2 as usp_msg
    import usp_record_1_3_pb2 as usp_record
    HAVE_USP, IMPORT_ERR = True, ""
except ImportError as _e:  # vẫn chạy được ở chế độ --demo
    HAVE_USP, IMPORT_ERR = False, str(_e)

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(HERE, "usp_web_config.json")
UPLOADED_SCHEMA = os.path.join(HERE, "uploaded.schema")
DEVICES_FILE = os.path.join(HERE, "usp_web_devices.json")
MAX_BODY = 30 * 1024 * 1024

DEFAULT_CFG = {
    "broker": "127.0.0.1",
    "port": "1883",
    "username": "",
    "password": "",
    "controller_id": "usp/controller",
    "controller_topic": "/usp/controller/",
    "agent_topic": "/usp/agent/",
    "agent_id": "",
    "schema_path": "",
}

# ----------------------------------------------------------------------------
# Định nghĩa các trang (gửi sang trình duyệt). Kiểu trường: s=chuỗi, b=bật/tắt, p=mật khẩu, ro=chỉ xem
# mode "table": mỗi dòng là một instance base.N. ; mode "deep": liệt kê mọi object con có tham số
# ----------------------------------------------------------------------------
X = "Device.X_001DAA."
MG = X + "System_Maintenance.Management."
MON = X + "Monitoring."
WL = X + "Wireless."

PAGES = [
    {"id": "fleet", "title": "Danh sách router", "type": "fleet"},
    {"id": "dashboard", "title": "Tổng quan", "type": "dashboard"},
    {"id": "wan", "title": "WAN", "type": "sections", "sections": [
        {"id": "wan_conn", "title": "Kết nối WAN", "base": X + "Configuration.WAN.WAN_Connections_R.",
         "cols": [["Name", "Tên"], ["Enabled", "Bật"], ["Physical_Type", "Loại"],
                  ["IPv4_Connection_Type", "Kiểu IPv4"], ["IPv4_IP_Address_Display", "IP"]],
         "fields": [["Name", "ro"], ["Enabled", "b"], ["Interface_Mode", "s"], ["Default_Route", "b"],
                    ["Physical_Type", "s"], ["Physical_Interface", "s"], ["IP_Version", "s"],
                    ["IPv4_Connection_Type", "s"], ["IPv4_Username", "s"], ["IPv4_Password", "p"],
                    ["IPv4_IP_Address_Display", "ro"], ["IPv4_IP_Address", "s"], ["IPv4_Subnet_Mask", "s"],
                    ["IPv4_Gateway_IP", "s"], ["DNS_IPv4_Primary_Server", "s"],
                    ["DNS_IPv4_Secondary_Server", "s"], ["Ethernet_Customer_VLAN", "b"],
                    ["Ethernet_Customer_Tag", "s"], ["Ethernet_Customer_Priority", "s"], ["WAN_MTU", "s"],
                    ["Detection_Mode", "s"], ["Pri_Ping_IP", "s"], ["Sec_Ping_IP", "s"]]},
        {"id": "wan_status", "title": "Trạng thái", "base": MON + "WAN.WAN_Status_Tabs.", "mode": "deep",
         "readonly": True},
        {"id": "wan_util", "title": "Băng thông", "base": MON + "WAN.WAN_Utilization.", "mode": "deep",
         "readonly": True},
        {"id": "wan_lb", "title": "Load balance / Failover", "base": X + "Configuration.WAN.LB_FAILOVER.",
         "fields": []},
        {"id": "wan_ddns", "title": "Dynamic DNS", "base": X + "Configuration.WAN.Dynamic_DNS.", "fields": []},
    ]},
    {"id": "lan", "title": "LAN / VLAN", "type": "sections", "sections": [
        {"id": "lan_net", "title": "Mạng LAN", "base": X + "Configuration.LAN.LAN_Networks_R.",
         "cols": [["Name", "Tên"], ["Enabled", "Bật"], ["IP_Address", "IP"], ["Subnet_Mask", "Mask"],
                  ["IPv4_DHCP_Server", "DHCP"]],
         "fields": [["Name", "ro"], ["Enabled", "b"], ["IPv4_Enable", "s"], ["IP_Address", "s"],
                    ["Subnet_Mask", "s"], ["MTU", "s"], ["IPv4_DHCP_Server", "s"], ["Start_IP_Address", "s"],
                    ["IP_Pool_Count", "s"], ["End_IP_Address", "ro"], ["Gateway_IP_Address", "s"],
                    ["Lease_Time", "s"], ["Primary_DNS", "s"], ["Secondary_DNS", "s"]]},
        {"id": "lan_vlan", "title": "VLAN", "base": X + "Configuration.LAN.ETH_VLAN.",
         "cols": [["Name", "Tên"], ["VLAN_Name", "VLAN"], ["LAN_Network", "LAN"], ["VLAN_Priority", "Ưu tiên"]],
         "fields": [["Name", "ro"], ["Default_VLAN", "b"], ["Default_VLAN_Name", "s"], ["VLAN_Name", "s"],
                    ["LAN_Network", "s"], ["VLAN_Priority", "s"], ["Access_Policy", "s"],
                    ["Group_Policy", "s"]]},
        {"id": "lan_status", "title": "Trạng thái LAN", "base": X + "Configuration.LAN.LAN_Status.",
         "readonly": True,
         "cols": [["Interface", "Cổng"], ["ipaddr", "IP"], ["mask", "Mask"], ["Status", "Trạng thái"],
                  ["mac_address", "MAC"], ["TX_Rate", "TX"], ["RX_Rate", "RX"]]},
        {"id": "lan_clients", "title": "Client", "base": MON + "Clients_List.Clients_List.", "readonly": True,
         "cols": [["Device_Name", "Tên"], ["IP", "IP"], ["MAC", "MAC"], ["LANSubnet", "Subnet"],
                  ["StartTime", "Bắt đầu"]]},
        {"id": "lan_dhcp", "title": "DHCP", "base": MON + "DHCP_Table.", "mode": "deep", "readonly": True},
        {"id": "lan_arp", "title": "ARP", "base": MON + "ARP_Table.", "mode": "deep", "readonly": True},
    ]},
    {"id": "wireless", "title": "Wireless", "type": "sections", "sections": [
        {"id": "wl_profile", "title": "AP Profile", "base": WL + "AP_Profile.AP_Profile.",
         "cols": [["Name", "Tên"]],
         "fields": [["Name", "ro"], ["Set_Mode", "s"], ["WLAN2G_Enabled", "b"], ["WLAN2G_Mode", "s"],
                    ["WLAN2G_TX_Power", "s"], ["WLAN2G_Channel", "s"], ["WLAN2G_Bandwidth", "s"],
                    ["WLAN2G_Country_Code", "s"], ["WLAN5G_Enabled", "b"], ["WLAN5G_Mode", "s"],
                    ["WLAN5G_TX_Power", "s"], ["WLAN5G_Channel", "s"], ["WLAN5G_Bandwidth", "s"],
                    ["WLAN5G_Country_Code", "s"], ["Band_Steering", "s"], ["Band_Steering_Threshold", "s"],
                    ["Enabled_802_11r", "b"], ["Enabled_802_11k", "b"], ["Roaming_Link_Rate_Enabled", "b"],
                    ["Link_Rate_Threshold", "s"], ["Roaming_By_Signal_Enabled", "b"],
                    ["Roaming_By_Signal_Threshold", "s"]]},
        {"id": "wl_aps", "title": "Thiết bị AP", "base": WL + "Device.Device_List.", "readonly": True,
         "cols": [["Device_Name", "Tên"], ["Device_IP_Address", "IP"], ["Device_Status", "Trạng thái"],
                  ["Device_Model", "Model"], ["Num_of_WLAN_Clients", "Client"], ["Firmware_Version", "Firmware"]]},
        {"id": "wl_overview", "title": "Tổng quan", "base": MG + "Wireless_Overview.", "readonly": True,
         "cols": [["Name", "Tên"], ["WLAN2G_SSID", "SSID 2.4G"], ["WLAN2G_MAC", "MAC 2.4G"],
                  ["WLAN5G_SSID", "SSID 5G"], ["WLAN5G_MAC", "MAC 5G"]]},
        {"id": "wl_clients", "title": "Client WiFi", "base": MG + "Wireless_Clients_Overview.", "mode": "deep",
         "readonly": True},
        {"id": "wl_ssid", "title": "WiFi (TR-181)", "base": "Device.WiFi.SSID.", "fields": []},
        {"id": "wl_radio", "title": "Radio (TR-181)", "base": "Device.WiFi.Radio.", "fields": []},
    ]},
    {"id": "datamodel", "title": "Data Model", "type": "datamodel"},
]


def find_section(base):
    for p in PAGES:
        for s in p.get("sections", []):
            if s["base"] == base:
                return s
    return None


# ----------------------------------------------------------------------------
# Schema (file .schema xuất từ router): "path kiểu quyền" mỗi dòng
# ----------------------------------------------------------------------------
class Schema:
    def __init__(self):
        self.entries, self.path = [], ""

    def load(self, path):
        entries = []
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                p = line.split()
                if len(p) >= 3 and p[0].startswith("Device."):
                    entries.append((p[0], p[1].replace("xsd:", ""), p[2]))
        if not entries:
            raise ValueError("File không có dòng nào dạng 'Device.xxx kiểu quyền'")
        self.entries, self.path = entries, path
        return len(entries)

    def search(self, q, limit=300):
        toks = [t for t in q.lower().split() if t]
        out = []
        for path, typ, rw in self.entries:
            lp = path.lower()
            if all(t in lp for t in toks):
                out.append([path, typ, rw])
                if len(out) >= limit:
                    break
        return out


# ----------------------------------------------------------------------------
# Danh sách router (Agent). Tự thêm khi router gửi bản tin tới, hoặc thêm tay.
# ----------------------------------------------------------------------------
class Devices:
    def __init__(self, log, persist=True):
        self.log, self.persist = log, persist
        self.items = {}
        self._lock = threading.Lock()
        if persist and os.path.exists(DEVICES_FILE):
            try:
                with open(DEVICES_FILE, encoding="utf-8") as f:
                    for d in json.load(f):
                        self._new(d["id"], d.get("name") or d["id"], d.get("topic", ""))
            except Exception as e:
                log(f"[!] Không đọc được danh sách router: {e}")

    def _new(self, did, name, topic):
        self.items[did] = {"id": did, "name": name, "topic": topic, "last_seen": 0, "status": "?",
                           "info": {}, "error": ""}
        return self.items[did]

    def _save(self):
        if not self.persist:
            return
        try:
            with open(DEVICES_FILE, "w", encoding="utf-8") as f:
                json.dump([{"id": d["id"], "name": d["name"], "topic": d["topic"]} for d in self.items.values()],
                          f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.log(f"[!] Không lưu được danh sách router: {e}")

    def seen(self, did):
        with self._lock:
            d = self.items.get(did)
            if not d:
                d = self._new(did, did, "")
                self._save()
                self.log(f"Phát hiện router mới: {did}")
            d["last_seen"] = time.time()

    def ensure(self, did):
        with self._lock:
            if did and did not in self.items:
                self._new(did, did, "")
                self._save()

    def upsert(self, did, name, topic):
        with self._lock:
            d = self.items.get(did) or self._new(did, did, "")
            d["name"], d["topic"] = name or did, topic
            self._save()

    def delete(self, did):
        with self._lock:
            self.items.pop(did, None)
            self._save()

    def get(self, did):
        return self.items.get(did)

    def topic(self, did):
        d = self.items.get(did)
        return d["topic"] if d else ""

    def set_status(self, did, status, info, error):
        with self._lock:
            d = self.items.get(did)
            if d:
                d["status"], d["error"] = status, error
                if info is not None:
                    d["info"] = info

    def list(self):
        with self._lock:
            return sorted((dict(d) for d in self.items.values()), key=lambda d: d["name"].lower())


# ----------------------------------------------------------------------------
# USP client (MQTT) - yêu cầu/phản hồi đồng bộ theo msg_id, an toàn đa luồng
# ----------------------------------------------------------------------------
def summarize_set(resp):
    ok, lines = True, []
    try:
        for r in resp.body.response.set_resp.updated_obj_results:
            st = r.oper_status
            if st.WhichOneof("oper_status") == "oper_failure":
                ok = False
                lines.append(f"{r.requested_path}: {st.oper_failure.err_code} {st.oper_failure.err_msg}".strip())
                for f in st.oper_failure.updated_inst_failures:
                    for pe in f.param_errs:
                        lines.append(f"  {pe.param}: {pe.err_code} {pe.err_msg}")
            else:
                for ir in st.oper_success.updated_inst_results:
                    for pe in ir.param_errs:
                        ok = False
                        lines.append(f"  {pe.param}: {pe.err_code} {pe.err_msg}")
    except Exception:
        text = str(resp.body.response)[:2000]
        return ("oper_failure" not in text), text
    return ok, "\n".join(lines) if lines else "Router đã nhận lệnh Set."


class UspClient:
    demo = False

    def __init__(self, log, devices):
        self.log = log
        self.devices = devices
        self.client = None
        self.cfg = dict(DEFAULT_CFG)
        self.connected = False
        self.status = "Chưa kết nối"
        self.waiters = {}
        self._counter = 0
        self._lock = threading.Lock()

    # --- kết nối ---------------------------------------------------------------
    def connect(self, cfg):
        self.disconnect()
        self.cfg = dict(cfg)
        self.devices.ensure((cfg.get("agent_id") or "").strip())
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if cfg.get("username"):
            c.username_pw_set(cfg["username"], cfg.get("password", ""))
        c.on_connect = self._on_connect
        c.on_disconnect = self._on_disconnect
        c.on_message = self._on_message
        self.client = c
        self.status = "Đang kết nối..."
        c.connect_async(cfg["broker"], int(cfg["port"]), 60)
        c.loop_start()
        self.log(f"Kết nối MQTT {cfg['broker']}:{cfg['port']} ...")

    def disconnect(self):
        if self.client:
            try:
                self.client.loop_stop()
                self.client.disconnect()
            except Exception:
                pass
            self.client = None
        self.connected = False
        self.status = "Đã ngắt kết nối"

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code.is_failure:
            self.connected, self.status = False, f"Kết nối lỗi: {reason_code}"
            self.log(self.status)
            return
        client.subscribe(self.cfg["controller_topic"])
        self.connected = True
        self.status = f"Đã kết nối - nghe tại {self.cfg['controller_topic']}"
        self.log(self.status)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        self.connected, self.status = False, "Mất kết nối MQTT"
        self.log(self.status)

    def _on_message(self, client, userdata, m):
        try:
            rec = usp_record.Record()
            rec.ParseFromString(m.payload)
            msg = usp_msg.Msg()
            msg.ParseFromString(rec.no_session_context.payload)
        except Exception as e:
            self.log(f"[!] Lỗi giải mã gói tin: {e}")
            return
        self.devices.seen(rec.from_id)
        name = usp_msg.Header.MsgType.Name(msg.header.msg_type)
        self.log(f"<<< {name} từ {rec.from_id} (id={msg.header.msg_id})")
        if msg.header.msg_type == usp_msg.Header.NOTIFY:  # phải trả NotifyResp
            try:
                resp = usp_msg.Msg()
                resp.header.msg_id = msg.header.msg_id
                resp.header.msg_type = usp_msg.Header.NOTIFY_RESP
                resp.body.response.notify_resp.subscription_id = msg.body.request.notify.subscription_id
                self._publish(resp, rec.from_id)
            except Exception as e:
                self.log(f"[!] Không gửi được NotifyResp: {e}")
            return
        w = self.waiters.get(msg.header.msg_id)
        if w:
            w["msg"] = msg
            w["ev"].set()

    # --- gửi/nhận ------------------------------------------------------------------
    def _next_id(self):
        with self._lock:
            self._counter += 1
            return f"{int(time.time())}-{self._counter}"

    def _new(self, mtype):
        msg = usp_msg.Msg()
        msg.header.msg_id = self._next_id()
        msg.header.msg_type = mtype
        return msg

    def _publish(self, msg, to_id):
        rec = usp_record.Record()
        rec.version = "1.3"
        rec.to_id = to_id
        rec.from_id = self.cfg["controller_id"]
        rec.payload_security = usp_record.Record.PLAINTEXT
        rec.no_session_context.payload = msg.SerializeToString()
        topic = self.devices.topic(to_id) or self.cfg["agent_topic"]
        self.client.publish(topic, rec.SerializeToString())
        self.log(f">>> {usp_msg.Header.MsgType.Name(msg.header.msg_type)} tới {to_id} (id={msg.header.msg_id})")

    def request(self, msg, agent_id, timeout=30):
        if not self.client or not self.connected:
            raise RuntimeError("Chưa kết nối MQTT broker.")
        if not agent_id:
            raise RuntimeError("Chưa chọn router.")
        w = {"ev": threading.Event(), "msg": None}
        mid = msg.header.msg_id
        self.waiters[mid] = w
        try:
            self._publish(msg, agent_id)
            if not w["ev"].wait(timeout):
                raise TimeoutError(f"Router không trả lời sau {timeout} giây.")
            return w["msg"]
        finally:
            self.waiters.pop(mid, None)

    @staticmethod
    def _err_text(resp):
        e = resp.body.error
        return f"{e.err_code}: {e.err_msg}"

    # --- thao tác ------------------------------------------------------------------
    def get(self, agent_id, paths, timeout=30):
        msg = self._new(usp_msg.Header.GET)
        msg.body.request.get.param_paths.extend(paths)
        resp = self.request(msg, agent_id, timeout)
        if resp.header.msg_type == usp_msg.Header.ERROR:
            raise RuntimeError(self._err_text(resp))
        out = []
        for rpr in resp.body.response.get_resp.req_path_results:
            item = {"requested": rpr.requested_path, "error": None, "objects": []}
            if rpr.err_code:
                item["error"] = {"code": rpr.err_code, "message": rpr.err_msg}
            for res in rpr.resolved_path_results:
                item["objects"].append({"path": res.resolved_path, "params": dict(res.result_params)})
            out.append(item)
        return out

    def set(self, agent_id, obj_path, params):
        msg = self._new(usp_msg.Header.SET)
        s = msg.body.request.set
        s.allow_partial = False
        obj = s.update_objs.add()
        obj.obj_path = obj_path
        for k, v in params.items():
            p = obj.param_settings.add()
            p.param, p.value, p.required = k, v, True
        resp = self.request(msg, agent_id)
        if resp.header.msg_type == usp_msg.Header.ERROR:
            return {"ok": False, "message": self._err_text(resp)}
        ok, text = summarize_set(resp)
        return {"ok": ok, "message": text}

    def operate(self, agent_id, command):
        msg = self._new(usp_msg.Header.OPERATE)
        op = msg.body.request.operate
        op.command, op.command_key, op.send_resp = command, "web", True
        resp = self.request(msg, agent_id)
        if resp.header.msg_type == usp_msg.Header.ERROR:
            return {"ok": False, "message": self._err_text(resp)}
        return {"ok": True, "message": str(resp.body.response)[:2000] or "Router đã nhận lệnh."}


class DemoUsp:
    """Dữ liệu giả để xem thử giao diện khi chưa có router."""
    demo = True
    connected = True
    MODELS = ["Vigor2928", "Vigor2865", "Vigor3910"]
    status = "DEMO - dữ liệu giả, không gửi gì tới router"

    def __init__(self, log):
        self.log = log
        self.store = {}

    def connect(self, cfg):
        self.log("Demo: bỏ qua kết nối MQTT")

    def disconnect(self):
        pass

    def _fake(self, sec):
        fields = [f[0] for f in sec.get("fields", [])] or ["Name", "Enable", "Value"]
        objs = []
        if sec.get("mode") == "deep":
            for i in (1, 2):
                objs.append({"path": f"{sec['base']}{i}.Item.1.",
                             "params": {"Name": f"demo-{i}", "Value": str(random.randint(1, 99))}})
            return objs
        for i in (1, 2, 3):
            params = {}
            for f in fields:
                if f == "Name":
                    params[f] = f"[DEMO] {sec['title']} {i}"
                elif re.search(r"enable", f, re.I):
                    params[f] = "1" if i != 3 else "0"
                elif re.search(r"pass|psk|key", f, re.I):
                    params[f] = "demo-secret"
                else:
                    params[f] = f"demo {f.lower()}"[:40]
            for c in sec.get("cols", []):
                params.setdefault(c[0], f"demo {i}")
            if "IP_Address" in params:
                params["IP_Address"] = f"192.168.{i}.1"
                params["Subnet_Mask"] = "255.255.255.0"
            objs.append({"path": f"{sec['base']}{i}.", "params": params})
        return objs

    def _model(self, agent_id):
        return self.MODELS[sum(map(ord, agent_id)) % 3]

    def get(self, agent_id, paths, timeout=30):
        out = []
        for p in paths:
            item = {"requested": p, "error": None, "objects": []}
            if p == "Device.DeviceInfo.":
                item["objects"] = [
                    {"path": "Device.DeviceInfo.", "params": {
                        "ModelName": self._model(agent_id), "Manufacturer": "DrayTek", "SerialNumber": agent_id[-6:],
                        "SoftwareVersion": "5.0.0_demo", "HardwareVersion": "V1.0", "CountryCode": "vn",
                        "UpTime": str(8000 + int(time.time()) % 5000)}},
                    {"path": "Device.DeviceInfo.MemoryStatus.", "params": {"Total": "881364", "Free": "311800"}},
                    {"path": "Device.DeviceInfo.MemoryStatus.MemoryMonitor.", "params": {"MemUtilization": "64"}},
                    {"path": "Device.DeviceInfo.ProcessStatus.", "params": {"CPUUsage": str(random.randint(3, 20))}},
                ]
            else:
                sec = find_section(p)
                if sec:
                    item["objects"] = self._fake(sec)
                else:
                    item["error"] = {"code": 7026, "message": "Path is invalid (demo)"}
            out.append(item)
        return out

    def set(self, agent_id, obj_path, params):
        self.log(f"Demo SET {obj_path} {params}")
        return {"ok": True, "message": "DEMO: chưa gửi gì tới router thật."}

    def operate(self, agent_id, command):
        self.log(f"Demo OPERATE {command}")
        return {"ok": True, "message": "DEMO: chưa gửi lệnh tới router thật."}


# ----------------------------------------------------------------------------
# Trạng thái ứng dụng
# ----------------------------------------------------------------------------
class App:
    def __init__(self, demo, web_password):
        self.logs = deque(maxlen=500)
        self.demo = demo
        self.web_password = web_password
        self.cfg = dict(DEFAULT_CFG)
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, encoding="utf-8") as f:
                    self.cfg.update(json.load(f))
            except Exception:
                pass
        self.allowed_hosts = None  # đặt trong main() khi chỉ nghe trên localhost
        self.devices = Devices(self.log, persist=not demo)
        if demo and not self.devices.items:
            for i in (1, 2, 3):
                self.devices.upsert(f"oui::DEMO00000{i}", f"Router demo {i}", "")
        self.usp = DemoUsp(self.log) if demo else UspClient(self.log, self.devices)
        self.schema = Schema()
        self._auto_schema()

    def log(self, text):
        self.logs.append(f"[{time.strftime('%H:%M:%S')}] {text}")

    def save_cfg(self):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.log(f"[!] Không lưu được cấu hình: {e}")

    def _auto_schema(self):
        cands = []
        if self.cfg.get("schema_path"):
            cands.append(self.cfg["schema_path"])
        cands += sorted(glob.glob(os.path.join(HERE, "*.schema")), key=os.path.getmtime, reverse=True)
        for p in cands:
            if os.path.exists(p):
                try:
                    n = self.schema.load(p)
                    self.log(f"Đã nạp schema {os.path.basename(p)} ({n} path)")
                    return
                except Exception as e:
                    self.log(f"[!] Schema lỗi: {e}")

    def state(self):
        cfg = {k: v for k, v in self.cfg.items() if k != "password"}
        cfg["has_password"] = bool(self.cfg.get("password"))
        return {"connected": bool(self.usp.connected), "status": self.usp.status, "devices": len(self.devices.items),
                "demo": self.demo, "cfg": cfg,
                "schema": {"loaded": bool(self.schema.entries), "count": len(self.schema.entries),
                           "name": os.path.basename(self.schema.path)}}


APP = None


# ----------------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "USPWeb/1.0"

    def log_message(self, fmt, *args):
        pass

    def _auth_ok(self):
        pw = APP.web_password
        if not pw:
            return True
        h = self.headers.get("Authorization", "")
        if h.startswith("Basic "):
            try:
                given = base64.b64decode(h[6:]).decode("utf-8", "replace").split(":", 1)[-1]
                return hmac.compare_digest(given.encode(), pw.encode())
            except Exception:
                return False
        return False

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ValueError("Dữ liệu gửi lên quá lớn")
        return self.rfile.read(n) if n else b""

    def _guard(self, post=False):
        host = (self.headers.get("Host") or "").lower()
        if APP.allowed_hosts and host not in APP.allowed_hosts:
            self._send(403, "Host không hợp lệ", "text/plain; charset=utf-8")
            return False
        if post:
            origin = self.headers.get("Origin")
            if self.headers.get("X-USP-Web") != "1" or (origin and urlparse(origin).netloc.lower() != host):
                self._send(403, "Yêu cầu bị từ chối (CSRF)", "text/plain; charset=utf-8")
                return False
        if self._auth_ok():
            return True
        self._send(401, "Cần mật khẩu", "text/plain; charset=utf-8",
                   {"WWW-Authenticate": 'Basic realm="USP Web"'})
        return False

    def do_GET(self):
        if not self._guard():
            return
        u = urlparse(self.path)
        try:
            if u.path in ("/", "/index.html"):
                self._send(200, INDEX_HTML, "text/html; charset=utf-8")
            elif u.path == "/api/state":
                self._json(APP.state())
            elif u.path == "/api/pages":
                self._json(PAGES)
            elif u.path == "/api/devices":
                self._json({"devices": APP.devices.list()})
            elif u.path == "/api/log":
                self._json({"lines": list(APP.logs)})
            elif u.path == "/api/schema/search":
                q = parse_qs(u.query).get("q", [""])[0]
                self._json({"results": APP.schema.search(q), "loaded": bool(APP.schema.entries)})
            else:
                self._send(404, "Not found", "text/plain; charset=utf-8")
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def do_POST(self):
        if not self._guard(post=True):
            return
        u = urlparse(self.path)
        try:
            raw = self._body()
            if u.path == "/api/schema/upload":
                with open(UPLOADED_SCHEMA, "wb") as f:
                    f.write(raw)
                n = APP.schema.load(UPLOADED_SCHEMA)
                APP.cfg["schema_path"] = UPLOADED_SCHEMA
                APP.save_cfg()
                APP.log(f"Đã nạp schema tải lên ({n} path)")
                return self._json({"count": n})
            data = json.loads(raw.decode("utf-8") or "{}")
            self._json(self._api(u.path, data))
        except TimeoutError as e:
            self._json({"error": str(e)}, 504)
        except (ValueError, KeyError) as e:
            self._json({"error": f"Dữ liệu không hợp lệ: {e}"}, 400)
        except Exception as e:
            APP.log(f"[!] {u.path}: {e}")
            self._json({"error": str(e)}, 500)

    @staticmethod
    def _device(d):
        did = str(d.get("device") or "").strip()
        if not did:
            items = APP.devices.list()
            if len(items) != 1:
                raise ValueError("Chưa chọn router (chọn ở ô phía trên).")
            did = items[0]["id"]
        if not APP.devices.get(did):
            raise ValueError(f"Router không có trong danh sách: {did}")
        return did

    @staticmethod
    def _probe(did):
        try:
            res = APP.usp.get(did, ["Device.DeviceInfo."], timeout=10)
            if res[0]["error"]:
                raise RuntimeError(f"{res[0]['error']['code']}: {res[0]['error']['message']}")
            f = {}
            for o in res[0]["objects"]:
                for k, v in o["params"].items():
                    f[o["path"] + k] = v
            D = "Device.DeviceInfo."
            tot, free = f.get(D + "MemoryStatus.Total"), f.get(D + "MemoryStatus.Free")
            ram = f.get(D + "MemoryStatus.MemoryMonitor.MemUtilization")
            if ram is None and tot and free:
                ram = round((float(tot) - float(free)) / float(tot) * 100)
            info = {"model": f.get(D + "ModelName", ""), "firmware": f.get(D + "SoftwareVersion", ""),
                    "serial": f.get(D + "SerialNumber", ""), "uptime": f.get(D + "UpTime", ""),
                    "cpu": f.get(D + "ProcessStatus.CPUUsage"), "ram": ram}
            APP.devices.set_status(did, "online", info, "")
        except Exception as e:
            APP.devices.set_status(did, "offline", None, str(e))

    def _api(self, path, d):
        usp = APP.usp
        if path == "/api/device/save":
            did = str(d["id"]).strip()
            if not re.fullmatch(r"[\w:.\-/]{1,100}", did):
                raise ValueError("Agent ID chỉ gồm chữ, số, và các ký tự : . - _ /")
            topic = str(d.get("topic") or "").strip()
            if topic and not topic.startswith("/"):
                topic = "/" + topic
            APP.devices.upsert(did, str(d.get("name") or "").strip(), topic)
            return {"ok": True}
        if path == "/api/device/delete":
            APP.devices.delete(str(d["id"]))
            return {"ok": True}
        if path == "/api/devices/scan":
            ids = [i["id"] for i in APP.devices.list()]
            with ThreadPoolExecutor(max_workers=6) as ex:
                list(ex.map(self._probe, ids))
            return {"devices": APP.devices.list()}
        if path == "/api/connect":
            if not HAVE_USP and not APP.demo:
                raise RuntimeError(f"Thiếu thư viện/file: {IMPORT_ERR}")
            cfg = dict(APP.cfg)
            for k in DEFAULT_CFG:
                if k in d and k not in ("password", "schema_path"):
                    cfg[k] = str(d[k]).strip()
            if d.get("password") is not None:
                cfg["password"] = str(d["password"])
            int(cfg["port"])
            APP.cfg = cfg
            APP.save_cfg()
            usp.connect(cfg)
            return {"ok": True}
        if path == "/api/disconnect":
            usp.disconnect()
            return {"ok": True}
        if path == "/api/get":
            paths = d["paths"]
            if not paths or not all(isinstance(p, str) and p.startswith("Device.") for p in paths):
                raise ValueError("paths phải là danh sách path bắt đầu bằng 'Device.'")
            return {"results": usp.get(self._device(d), paths)}
        if path == "/api/set":
            obj, params = d["obj_path"], d["params"]
            if not (isinstance(obj, str) and obj.startswith("Device.") and obj.endswith(".")):
                raise ValueError("obj_path phải bắt đầu 'Device.' và kết thúc bằng '.'")
            if not params or not all(isinstance(k, str) and isinstance(v, str) for k, v in params.items()):
                raise ValueError("params phải là {tên: giá trị chuỗi}")
            APP.log(f"SET [{d.get('device')}] {obj} {list(params)}")  # không ghi giá trị (có thể là mật khẩu)
            return usp.set(self._device(d), obj, params)
        if path == "/api/operate":
            cmd = d["command"]
            if not (isinstance(cmd, str) and re.fullmatch(r"Device\.[A-Za-z0-9_.{}\-]+\(\)", cmd)):
                raise ValueError("command phải dạng Device.xxx()")
            APP.log(f"OPERATE {cmd}")
            return usp.operate(self._device(d), cmd)
        raise KeyError(f"API không tồn tại: {path}")


# ----------------------------------------------------------------------------
# Giao diện (HTML/CSS/JS một file, không dùng thư viện ngoài)
# ----------------------------------------------------------------------------
INDEX_HTML = r"""<!doctype html>
<html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>USP / TR-369 Controller</title>
<style>
:root{--bg:#161616;--panel:#212121;--panel2:#2b2b2b;--line:#3a3a3a;--text:#e6e6e6;--muted:#9a9a9a;--acc:#c62828;--acc2:#e53935;--ok:#66bb6a;--err:#ef5350}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,Segoe UI,Roboto,sans-serif;display:flex;min-height:100vh}
aside{width:200px;background:var(--panel);border-right:1px solid var(--line);padding:16px 10px;flex:none}
aside h1{font-size:18px;margin:0 8px 16px;color:var(--acc2)}
aside a{display:block;padding:9px 12px;border-radius:6px;color:var(--text);text-decoration:none;margin-bottom:3px;cursor:pointer}
aside a:hover{background:var(--panel2)} aside a.on{background:var(--acc);color:#fff}
main{flex:1;min-width:0;display:flex;flex-direction:column}
header{display:flex;gap:12px;align-items:center;padding:12px 20px;border-bottom:1px solid var(--line);background:var(--panel)}
.pill{padding:3px 10px;border-radius:12px;font-size:12px;background:#444}
.pill.ok{background:#1b5e20}.pill.err{background:#7f1d1d}
#view{padding:18px 20px;overflow:auto}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}
.tab{padding:6px 12px;border-radius:6px;background:var(--panel2);cursor:pointer;border:1px solid var(--line)}
.tab.on{background:var(--acc);border-color:var(--acc);color:#fff}
.bar{display:flex;gap:12px;align-items:center;margin-bottom:10px;flex-wrap:wrap}
.btn{background:var(--acc);color:#fff;border:0;padding:7px 14px;border-radius:6px;cursor:pointer;font:inherit}
.btn:hover{background:var(--acc2)} .btn.gray{background:#444}.btn.gray:hover{background:#666}
.btn:disabled{opacity:.5;cursor:default}
.muted{color:var(--muted)}.okc{color:var(--ok)}.errc{color:var(--err)}
.note{background:#3a2a10;border:1px solid #7a5a1a;padding:8px 12px;border-radius:6px;margin-bottom:10px}
.split{display:flex;gap:14px;align-items:flex-start;flex-wrap:wrap}
.list{flex:1 1 380px;min-width:300px;max-height:68vh;overflow:auto;border:1px solid var(--line);border-radius:6px;background:var(--panel)}
.detail{flex:1 1 420px;min-width:320px;max-height:68vh;overflow:auto;border:1px solid var(--line);border-radius:6px;background:var(--panel);padding:12px}
table.tbl{border-collapse:collapse;width:100%}
.tbl th{position:sticky;top:0;background:#1b1b1b;text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
.tbl td{padding:6px 10px;border-bottom:1px solid #2a2a2a;word-break:break-all}
.tbl tr.row{cursor:pointer}.tbl tr.row:hover{background:var(--panel2)}.tbl tr.sel{background:var(--acc)!important;color:#fff}
.form{display:grid;grid-template-columns:minmax(140px,auto) 1fr;gap:8px 12px;align-items:center}
.form label{color:#ccc;word-break:break-all}
input[type=text],input[type=password],input[type=number],textarea,select{width:100%;background:#343638;border:1px solid #565b5e;color:#fff;border-radius:5px;padding:6px 8px;font:inherit}
input:focus{outline:1px solid var(--acc);border-color:var(--acc)}
.pw{display:flex;gap:6px}.pw button{flex:none}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px}
.card h3{margin:0 0 10px;font-size:15px}
.kv{display:grid;grid-template-columns:110px 1fr;gap:5px 10px}.kv span:nth-child(odd){color:var(--muted)}
.bar2{height:10px;background:#3a3a3a;border-radius:5px;overflow:hidden;margin:4px 0 10px}.bar2>i{display:block;height:100%;background:var(--acc)}
pre{background:#111;border:1px solid var(--line);border-radius:6px;padding:10px;overflow:auto;max-height:50vh;white-space:pre-wrap;word-break:break-all}
#toasts{position:fixed;right:16px;bottom:16px;display:flex;flex-direction:column;gap:8px;z-index:9}
.toast{padding:10px 14px;border-radius:6px;max-width:460px;white-space:pre-wrap;box-shadow:0 2px 8px #000a}
.toast.ok{background:#1b5e20}.toast.err{background:#7f1d1d}
.chk{display:flex;gap:6px;align-items:center;cursor:pointer}.chk input{width:auto}
@media(max-width:760px){body{flex-direction:column}aside{width:auto;display:flex;flex-wrap:wrap;gap:4px}aside h1{width:100%}}
</style></head><body>
<aside><h1>USP Web</h1><nav id="nav"></nav></aside>
<main><header><select id="dev" title="Router đang điều khiển" style="width:240px"></select><span id="pill" class="pill">...</span><span id="agent" class="muted"></span><span id="demo" class="pill" style="display:none">DEMO</span></header>
<div id="view"></div></main>
<div id="toasts"></div>
<script>
"use strict";
const $ = (s, el = document) => el.querySelector(s);
function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) { if (v) e.addEventListener(k.slice(2), v); }
    else if (k === "value") e.value = v;
    else if (k === "checked") e.checked = !!v;
    else if (v !== false && v != null) e.setAttribute(k, v);
  }
  for (const c of kids.flat(Infinity)) {
    if (c == null || c === false) continue;
    e.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return e;
}
function toast(msg, ok = true) {
  const t = h("div", { class: "toast " + (ok ? "ok" : "err") }, msg);
  $("#toasts").append(t);
  setTimeout(() => t.remove(), ok ? 4000 : 9000);
}
async function api(path, body) {
  if (body && ["/api/get", "/api/set", "/api/operate"].includes(path) && body.device === undefined) body = { ...body, device: CUR };
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json", "X-USP-Web": "1" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  let j;
  try { j = await r.json(); } catch (e) { throw new Error("Phản hồi không hợp lệ (HTTP " + r.status + ")"); }
  if (!r.ok || j.error) throw new Error(j.error || ("HTTP " + r.status));
  return j;
}

const TRUE = ["1", "true", "True"];
const PW_RE = /pass|psk|secret|key/i;
let PAGES = [], STATE = {}, timers = [], DEVS = [], CUR = localStorage.getItem("dev") || "", devSig = "";
const fmtUpTime = s => { s = parseInt(s, 10); if (isNaN(s)) return ""; const d = Math.floor(s / 86400), hh = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60); return (d ? d + "d " : "") + hh + "h" + String(m).padStart(2, "0"); };
function renderDevSelect(force) {
  const sel = $("#dev");
  const sig = JSON.stringify(DEVS.map(d => [d.id, d.name, d.status])) + "|" + CUR;
  if (!force && sig === devSig) return;
  devSig = sig;
  sel.replaceChildren(...(DEVS.length ? DEVS.map(d => h("option", { value: d.id }, (d.status === "online" ? "● " : d.status === "offline" ? "○ " : "") + d.name)) : [h("option", { value: "" }, "(chưa có router)")]));
  sel.value = CUR;
}
async function refreshDevices() {
  try { DEVS = (await api("/api/devices")).devices; } catch (e) { return; }
  if (!DEVS.some(d => d.id === CUR)) { CUR = DEVS[0] ? DEVS[0].id : ""; localStorage.setItem("dev", CUR); }
  renderDevSelect(false);
}
function clearTimers() { timers.forEach(clearInterval); timers = []; }

/* ---------- xử lý dữ liệu GET ---------- */
function relIdx(path, base) {
  if (!path.startsWith(base)) return null;
  const m = path.slice(base.length).match(/^(\d+)\.$/);
  return m ? m[1] : null;
}
function tableRows(objs, base) {
  const out = [];
  for (const o of objs) { const i = relIdx(o.path, base); if (i !== null) out.push({ id: i, vals: o.params }); }
  return out.sort((a, b) => a.id - b.id);
}
function deepRows(objs, base) {
  const out = [];
  for (const o of objs) {
    if (!o.path.startsWith(base) || !Object.keys(o.params).length) continue;
    out.push({ id: o.path.slice(base.length) || "(gốc)", vals: o.params });
  }
  return out;
}
function flatten(objs) {
  const f = {};
  for (const o of objs) for (const [k, v] of Object.entries(o.params)) f[o.path + k] = v;
  return f;
}
function dataTable(cols, rows, onSelect) {
  const t = h("table", { class: "tbl" });
  t.append(h("thead", {}, h("tr", {}, cols.map(c => h("th", {}, c[1])))));
  const tb = h("tbody");
  rows.forEach(r => {
    const tr = h("tr", { class: onSelect ? "row" : "", "data-id": r.id, onclick: onSelect ? () => onSelect(r) : null },
      cols.map((c, i) => {
        let v = c[0] === "_id" ? r.id : (r.vals[c[0]] ?? "");
        if (i === 0 && v === "") v = "#" + r.id;
        return h("td", {}, v);
      }));
    tb.append(tr);
  });
  if (!rows.length) tb.append(h("tr", {}, h("td", { colspan: cols.length, class: "muted" }, "Không có dữ liệu")));
  t.append(tb);
  return t;
}

/* ---------- trang dạng section (WAN / LAN / Wireless) ---------- */
function sectionView(sec) {
  const root = h("div", {});
  const status = h("span", { class: "muted" }, "");
  const allBox = h("input", { type: "checkbox", onchange: () => renderDetail() });
  const listBox = h("div", { class: "list" }), detailBox = h("div", { class: "detail" });
  root.append(
    sec.note ? h("div", { class: "note" }, sec.note) : null,
    h("div", { class: "bar" },
      h("button", { class: "btn", onclick: () => load(sel) }, "Tải lại"),
      sec.readonly ? null : h("label", { class: "chk" }, allBox, "Hiện tất cả trường"),
      status),
    h("div", { class: "split" }, listBox, detailBox));
  let rows = [], sel = null;
  const setStatus = (t, ok) => { status.textContent = t; status.className = ok === undefined ? "muted" : (ok ? "okc" : "errc"); };

  function columns() {
    if (sec.cols && sec.cols.length) return sec.cols;
    const keys = [];
    rows.forEach(r => Object.keys(r.vals).forEach(k => { if (!keys.includes(k) && !k.endsWith("NumberOfEntries")) keys.push(k); }));
    const ks = keys.slice(0, 8).map(k => [k, k]);
    return sec.mode === "deep" ? [["_id", "Đường dẫn"], ...ks] : ks;
  }
  function renderList() {
    listBox.replaceChildren(dataTable(columns(), rows, r => select(r.id)));
    markSel();
  }
  function markSel() {
    listBox.querySelectorAll("tr.row").forEach(tr => tr.classList.toggle("sel", tr.dataset.id === String(sel)));
  }
  function select(id) { sel = id; markSel(); renderDetail(); }

  function renderDetail() {
    detailBox.replaceChildren();
    const r = rows.find(x => String(x.id) === String(sel));
    if (!r) { detailBox.append(h("div", { class: "muted" }, "Chọn một dòng ở bên trái.")); return; }
    const wl = (sec.fields || []).filter(f => f[0] in r.vals);
    if (sec.readonly) {
      const keys = wl.map(f => f[0]);
      Object.keys(r.vals).sort().forEach(k => { if (!keys.includes(k)) keys.push(k); });
      detailBox.append(h("div", { class: "form" }, keys.map(k => [h("label", {}, k), h("div", {}, r.vals[k] || "-")])));
      return;
    }
    const defs = wl.map(f => [f[0], f[1]]);
    if (allBox.checked || !(sec.fields || []).length) {
      const known = new Set(defs.map(d => d[0]));
      Object.keys(r.vals).sort().forEach(k => { if (!known.has(k) && !k.endsWith("NumberOfEntries")) defs.push([k, PW_RE.test(k) ? "p" : "s"]); });
    }
    const widgets = {}, grid = h("div", { class: "form" });
    defs.forEach(([name, kind]) => {
      const v = r.vals[name] ?? "";
      let el;
      if (kind === "b") el = h("input", { type: "checkbox", checked: TRUE.includes(v) });
      else if (kind === "ro") el = h("div", { class: "muted" }, v || "-");
      else if (kind === "p") {
        const inp = h("input", { type: "password", value: v });
        const btn = h("button", { class: "btn gray", type: "button" }, "Hiện");
        btn.addEventListener("click", () => { const show = inp.type === "password"; inp.type = show ? "text" : "password"; btn.textContent = show ? "Ẩn" : "Hiện"; });
        el = h("div", { class: "pw" }, inp, btn); el._input = inp;
      } else el = h("input", { type: "text", value: v });
      widgets[name] = { kind, el, orig: v };
      grid.append(h("label", {}, name), el);
    });
    const save = h("button", { class: "btn", onclick: () => doSave(r, widgets) }, "Lưu thay đổi");
    detailBox.append(grid, h("div", { class: "bar", style: "margin-top:14px" }, save,
      h("button", { class: "btn gray", onclick: () => renderDetail() }, "Hoàn tác")));
  }

  async function doSave(r, widgets) {
    const changes = {};
    for (const [name, w] of Object.entries(widgets)) {
      if (w.kind === "ro") continue;
      if (w.kind === "b") {
        const now = w.el.checked;
        if (now !== TRUE.includes(w.orig)) changes[name] = ["true", "false"].includes(w.orig.toLowerCase()) ? String(now) : (now ? "1" : "0");
      } else {
        const now = w.kind === "p" ? w.el._input.value : w.el.value;
        if (now !== w.orig) changes[name] = now;
      }
    }
    const names = Object.keys(changes);
    if (!names.length) { toast("Không có thay đổi nào để lưu."); return; }
    const objPath = sec.base + r.id + ".";
    const detail = names.map(n => "  " + n + " = " + (widgets[n].kind === "p" ? "******" : changes[n])).join("\n");
    if (!confirm("Set " + names.length + " tham số cho\n" + objPath + "\n\n" + detail)) return;
    setStatus("Đang gửi...");
    try {
      const res = await api("/api/set", { obj_path: objPath, params: changes });
      toast((res.ok ? "✔ " : "✖ ") + res.message, res.ok);
      if (res.ok) load(r.id); else setStatus("Router từ chối, xem thông báo.", false);
    } catch (e) { toast("✖ " + e.message, false); setStatus(e.message, false); }
  }

  async function load(keep) {
    setStatus("Đang tải... (bảng lớn có thể mất vài giây)");
    try {
      const res = await api("/api/get", { paths: [sec.base] });
      const q = res.results[0];
      if (q.error) { setStatus("✖ " + q.error.code + ": " + q.error.message, false); return; }
      rows = sec.mode === "deep" ? deepRows(q.objects, sec.base) : tableRows(q.objects, sec.base);
      setStatus("Đã tải " + rows.length + " mục lúc " + new Date().toLocaleTimeString(), true);
      sel = rows.some(x => String(x.id) === String(keep)) ? keep : (rows[0] ? rows[0].id : null);
      renderList(); renderDetail();
    } catch (e) { setStatus("✖ " + e.message, false); }
  }
  setTimeout(() => load(null), 0);
  return root;
}

function viewSections(page, root) {
  const body = h("div", {});
  const tabs = h("div", { class: "tabs" });
  let cur = Number(sessionStorage.getItem("sec_" + page.id) || 0);
  if (!(cur >= 0 && cur < page.sections.length)) cur = 0;
  function show(i) {
    cur = i; sessionStorage.setItem("sec_" + page.id, i);
    tabs.querySelectorAll(".tab").forEach((t, j) => t.classList.toggle("on", j === i));
    body.replaceChildren(sectionView(page.sections[i]));
  }
  page.sections.forEach((s, i) => tabs.append(h("div", { class: "tab", onclick: () => show(i) }, s.title)));
  root.append(tabs, body);
  show(cur);
}

/* ---------- Danh sách router ---------- */
function viewFleet(root) {
  const holder = h("div", { class: "list", style: "max-height:none" });
  const note = h("span", { class: "muted" }, "");
  const auto = h("input", { type: "checkbox", onchange: () => { clearTimers(); if (auto.checked) timers.push(setInterval(scan, 60000)); } });
  async function scan() {
    note.textContent = "Đang quét...";
    try { await api("/api/devices/scan", {}); note.textContent = "Đã quét lúc " + new Date().toLocaleTimeString(); }
    catch (e) { note.textContent = "✖ " + e.message; }
    draw();
  }
  async function save(d) { try { await api("/api/device/save", d); draw(); } catch (e) { toast("✖ " + e.message, false); } }
  function pick(id) {
    CUR = id; localStorage.setItem("dev", id); renderDevSelect(true);
    if (location.hash === "#dashboard") route(); else location.hash = "#dashboard";
  }
  function add() {
    const id = prompt("Agent ID của router (ví dụ oui::1449BC9EB7D8):"); if (!id) return;
    const name = prompt("Tên hiển thị:", id); if (name === null) return;
    const topic = prompt("Agent Topic riêng (để trống = dùng chung ở trang Kết nối):", ""); if (topic === null) return;
    save({ id: id.trim(), name, topic });
  }
  function edit(d) {
    const name = prompt("Tên hiển thị:", d.name); if (name === null) return;
    const topic = prompt("Agent Topic riêng (để trống = dùng chung):", d.topic || ""); if (topic === null) return;
    save({ id: d.id, name, topic });
  }
  async function remove(d) {
    if (!confirm("Xóa router “" + d.name + "” khỏi danh sách? (không ảnh hưởng tới router)")) return;
    try { await api("/api/device/delete", { id: d.id }); draw(); } catch (e) { toast("✖ " + e.message, false); }
  }
  async function draw() {
    await refreshDevices();
    const t = h("table", { class: "tbl" });
    t.append(h("thead", {}, h("tr", {}, ["Tên", "Agent ID", "Model", "Firmware", "Uptime", "CPU", "RAM", "Trạng thái", "Thấy lần cuối", ""].map(x => h("th", {}, x)))));
    const tb = h("tbody");
    DEVS.forEach(d => {
      const i = d.info || {};
      tb.append(h("tr", { class: d.id === CUR ? "sel" : "" },
        h("td", {}, d.name), h("td", {}, d.id), h("td", {}, i.model || ""), h("td", {}, i.firmware || ""),
        h("td", {}, i.uptime ? fmtUpTime(i.uptime) : ""), h("td", {}, i.cpu != null ? i.cpu + "%" : ""), h("td", {}, i.ram != null ? i.ram + "%" : ""),
        h("td", { class: d.status === "online" ? "okc" : (d.status === "offline" ? "errc" : "muted"), title: d.error || "" },
          d.status === "online" ? "● online" : (d.status === "offline" ? "○ offline" : "? chưa quét")),
        h("td", {}, d.last_seen ? new Date(d.last_seen * 1000).toLocaleTimeString() : ""),
        h("td", { style: "white-space:nowrap" }, h("button", { class: "btn", onclick: () => pick(d.id) }, "Mở"), " ",
          h("button", { class: "btn gray", onclick: () => edit(d) }, "Sửa"), " ", h("button", { class: "btn gray", onclick: () => remove(d) }, "Xóa"))));
    });
    if (!DEVS.length) tb.append(h("tr", {}, h("td", { colspan: 10, class: "muted" }, "Chưa có router. Kết nối broker (trang Kết nối): router sẽ tự xuất hiện khi gửi bản tin, hoặc bấm “Thêm router”.")));
    t.append(tb); holder.replaceChildren(t);
  }
  root.append(h("div", { class: "bar" }, h("button", { class: "btn", onclick: scan }, "Quét trạng thái"),
    h("button", { class: "btn gray", onclick: add }, "Thêm router"), h("label", { class: "chk" }, auto, "Tự quét mỗi 60 giây"), note), holder);
  draw();
}

/* ---------- Dashboard ---------- */
function viewDashboard(root) {
  const info = h("div", { class: "card" }), res = h("div", { class: "card" });
  const wan = h("div", { class: "card" }), lan = h("div", { class: "card" });
  const auto = h("input", { type: "checkbox", onchange: () => { clearTimers(); if (auto.checked) timers.push(setInterval(loadInfo, 5000)); } });
  root.append(h("div", { class: "bar" }, h("button", { class: "btn", onclick: loadAll }, "Làm mới"),
    h("label", { class: "chk" }, auto, "Tự làm mới 5 giây (thông tin & CPU/RAM)")),
    h("div", { class: "cards" }, info, res, wan, lan));
  const D = "Device.DeviceInfo.";
  const fmtUp = s => { s = parseInt(s, 10); if (isNaN(s)) return "-"; const d = Math.floor(s / 86400), hh = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60), ss = s % 60; return (d ? d + "d " : "") + [hh, m, ss].map(x => String(x).padStart(2, "0")).join(":"); };
  const bar = (label, pct, extra) => [h("div", {}, label + ": " + pct + "%" + (extra || "")), h("div", { class: "bar2" }, h("i", { style: "width:" + Math.min(100, Math.max(0, pct)) + "%" }))];
  async function loadInfo() {
    try {
      const f = flatten((await api("/api/get", { paths: [D] })).results[0].objects);
      const rowsKV = [["Model", f[D + "ModelName"]], ["Hãng", f[D + "Manufacturer"]], ["Serial", f[D + "SerialNumber"]],
        ["Firmware", f[D + "SoftwareVersion"]], ["Hardware", f[D + "HardwareVersion"]], ["Quốc gia", f[D + "CountryCode"]], ["Uptime", fmtUp(f[D + "UpTime"])]];
      info.replaceChildren(h("h3", {}, "Thông tin thiết bị"), h("div", { class: "kv" }, rowsKV.map(([k, v]) => [h("span", {}, k), h("span", {}, v || "-")])));
      const cpu = parseFloat(f[D + "ProcessStatus.CPUUsage"] || 0);
      const tot = parseFloat(f[D + "MemoryStatus.Total"]), free = parseFloat(f[D + "MemoryStatus.Free"]);
      const ram = f[D + "MemoryStatus.MemoryMonitor.MemUtilization"] != null ? parseFloat(f[D + "MemoryStatus.MemoryMonitor.MemUtilization"]) : (tot ? (tot - free) / tot * 100 : 0);
      res.replaceChildren(h("h3", {}, "Tài nguyên"), ...bar("CPU", Math.round(cpu)),
        ...bar("RAM", Math.round(ram), tot ? "  (" + Math.round((tot - free) / 1024) + " / " + Math.round(tot / 1024) + " MB)" : ""));
    } catch (e) { info.replaceChildren(h("h3", {}, "Thông tin thiết bị"), h("div", { class: "errc" }, e.message)); }
  }
  async function loadTable(card, title, base, cols) {
    card.replaceChildren(h("h3", {}, title), h("div", { class: "muted" }, "Đang tải..."));
    try {
      const q = (await api("/api/get", { paths: [base] })).results[0];
      if (q.error) throw new Error(q.error.code + ": " + q.error.message);
      card.replaceChildren(h("h3", {}, title), h("div", { style: "overflow:auto" }, dataTable(cols, tableRows(q.objects, base))));
    } catch (e) { card.replaceChildren(h("h3", {}, title), h("div", { class: "errc" }, e.message)); }
  }
  function loadAll() {
    loadInfo();
    loadTable(wan, "WAN", "Device.X_001DAA.Configuration.WAN.WAN_Connections_R.",
      [["Name", "Tên"], ["Enabled", "Bật"], ["Physical_Type", "Loại"], ["IPv4_Connection_Type", "Kiểu"], ["IPv4_IP_Address_Display", "IP"]]);
    loadTable(lan, "LAN", "Device.X_001DAA.Configuration.LAN.LAN_Status.",
      [["Interface", "Cổng"], ["ipaddr", "IP"], ["mask", "Mask"], ["Status", "Trạng thái"]]);
  }
  loadAll();
}

/* ---------- Data Model ---------- */
function viewDataModel(root) {
  const q = h("input", { type: "text", placeholder: "Tìm path, ví dụ: DHCP, Firewall, SSID, Reboot ..." });
  const idx = h("input", { type: "text", value: "1", style: "width:90px" });
  const path = h("input", { type: "text", placeholder: "Device.DeviceInfo." });
  const val = h("input", { type: "text", placeholder: "Giá trị (khi Set)" });
  const out = h("pre", {}, "Kết quả sẽ hiện ở đây.");
  const list = h("div", { class: "list", style: "max-height:42vh" });
  const info = h("div", { class: "muted" }, "");
  let selType = "";

  const resolve = p => {
    const ids = idx.value.split(",").map(x => x.trim() || "1");
    const parts = p.split("{i}");
    let r = parts[0];
    parts.slice(1).forEach((s, n) => { r += ids[Math.min(n, ids.length - 1)] + s; });
    return r;
  };
  let timer = null;
  q.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(search, 300); });
  async function search() {
    try {
      const r = await api("/api/schema/search?q=" + encodeURIComponent(q.value));
      if (!r.loaded) { list.replaceChildren(h("div", { class: "note" }, "Chưa nạp file .schema. Vào trang “Kết nối” để tải lên, hoặc đặt file .schema cùng thư mục usp_web.py.")); return; }
      const rows = r.results.map(x => ({ id: x[0], vals: { p: x[0], t: x[1], w: x[2] === "1" ? "ghi" : "đọc" } }));
      info.textContent = rows.length + " kết quả" + (rows.length >= 300 ? " (đã cắt bớt, hãy gõ cụ thể hơn)" : "");
      list.replaceChildren(dataTable([["p", "Path"], ["t", "Kiểu"], ["w", "Quyền"]], rows, row => {
        path.value = resolve(row.id); selType = row.vals.t; check();
        list.querySelectorAll("tr.row").forEach(tr => tr.classList.toggle("sel", tr.dataset.id === row.id));
      }));
    } catch (e) { list.replaceChildren(h("div", { class: "errc" }, e.message)); }
  }
  idx.addEventListener("input", () => { const sel = list.querySelector("tr.sel"); if (sel) { path.value = resolve(sel.dataset.id); } });
  const check = () => { out.textContent = selType === "command" ? "Mục này là lệnh: dùng nút Operate." : (selType === "object" ? "Mục này là object: Get sẽ lấy cả nhánh (có thể rất dài)." : ""); };

  async function run(fn) { try { out.textContent = "Đang chờ router..."; out.textContent = await fn(); } catch (e) { out.textContent = "✖ " + e.message; } }
  const doGet = () => run(async () => {
    const r = await api("/api/get", { paths: [path.value.trim()] });
    const lines = [];
    r.results.forEach(x => {
      lines.push("▶ " + x.requested);
      if (x.error) lines.push("   ✖ " + x.error.code + ": " + x.error.message);
      x.objects.forEach(o => { lines.push("  " + o.path); Object.entries(o.params).forEach(([k, v]) => lines.push("      " + k + " = " + (PW_RE.test(k) ? "******" : v))); });
    });
    return lines.join("\n");
  });
  const doSet = () => run(async () => {
    const p = path.value.trim(), i = p.lastIndexOf(".");
    if (i < 0 || i === p.length - 1) throw new Error("Chọn một tham số (path không kết thúc bằng dấu chấm).");
    const obj = p.slice(0, i + 1), name = p.slice(i + 1);
    if (!confirm("SET " + p + " = " + val.value + " ?")) return "Đã hủy.";
    const r = await api("/api/set", { obj_path: obj, params: { [name]: val.value } });
    return (r.ok ? "✔ " : "✖ ") + r.message;
  });
  const doOp = () => run(async () => {
    const c = path.value.trim();
    if (!confirm("Thực thi lệnh " + c + " ?")) return "Đã hủy.";
    const r = await api("/api/operate", { command: c });
    return (r.ok ? "✔ " : "✖ ") + r.message;
  });
  root.append(
    h("div", { class: "bar" }, h("div", { style: "flex:1;min-width:240px" }, q), h("span", { class: "muted" }, "Số {i}:"), idx, info),
    list,
    h("div", { class: "bar", style: "margin-top:12px" }, h("div", { style: "flex:2;min-width:260px" }, path), h("div", { style: "flex:1;min-width:160px" }, val)),
    h("div", { class: "bar" }, h("button", { class: "btn", onclick: doGet }, "Get"), h("button", { class: "btn gray", onclick: doSet }, "Set"),
      h("button", { class: "btn gray", onclick: doOp }, "Operate")),
    out);
  search();
}

/* ---------- Log ---------- */
function viewLog(root) {
  const pre = h("pre", { style: "max-height:75vh" }, "");
  root.append(pre);
  async function tick() { try { pre.textContent = (await api("/api/log")).lines.join("\n"); pre.scrollTop = pre.scrollHeight; } catch (e) { /* bỏ qua */ } }
  tick(); timers.push(setInterval(tick, 2000));
}

/* ---------- Kết nối ---------- */
function viewConnect(root) {
  const c = STATE.cfg || {};
  const F = {};
  const fields = [["broker", "MQTT Broker IP"], ["port", "Port"], ["username", "Username (nếu có)"], ["password", "Password (nếu có)"],
    ["controller_id", "Controller EndpointID"], ["controller_topic", "Controller Topic (nghe)"], ["agent_topic", "Agent Topic (gửi)"],
    ["agent_id", "Thêm sẵn Agent ID (tuỳ chọn)"]];
  const grid = h("div", { class: "form", style: "max-width:560px" });
  fields.forEach(([k, label]) => {
    F[k] = h("input", { type: k === "password" ? "password" : "text", value: k === "password" ? "" : (c[k] ?? ""),
      placeholder: k === "password" && c.has_password ? "(giữ nguyên mật khẩu đã lưu)" : "" });
    grid.append(h("label", {}, label), F[k]);
  });
  const file = h("input", { type: "file", accept: ".schema,.txt", onchange: async () => {
    if (!file.files[0]) return;
    try {
      const text = await file.files[0].text();
      const r = await fetch("/api/schema/upload", { method: "POST", headers: { "Content-Type": "text/plain", "X-USP-Web": "1" }, body: text });
      const j = await r.json(); if (!r.ok || j.error) throw new Error(j.error || "Lỗi");
      toast("Đã nạp schema: " + j.count + " path"); refreshState();
    } catch (e) { toast("✖ " + e.message, false); }
  } });
  async function connect() {
    const body = {};
    fields.forEach(([k]) => { body[k] = F[k].value; });
    if (!F.password.value) body.password = null;
    try { await api("/api/connect", body); toast("Đang kết nối..."); setTimeout(refreshState, 800); } catch (e) { toast("✖ " + e.message, false); }
  }
  root.append(h("div", { class: "card", style: "max-width:620px" }, h("h3", {}, "Kết nối MQTT broker"), grid,
    h("div", { class: "bar", style: "margin-top:14px" }, h("button", { class: "btn", onclick: connect }, "Lưu & Kết nối"),
      h("button", { class: "btn gray", onclick: async () => { await api("/api/disconnect", {}); refreshState(); } }, "Ngắt kết nối"))),
    h("div", { class: "card", style: "max-width:620px;margin-top:14px" }, h("h3", {}, "File schema (.schema)"),
      h("div", { class: "muted", style: "margin-bottom:8px" }, STATE.schema && STATE.schema.loaded ? "Đang dùng: " + STATE.schema.name + " (" + STATE.schema.count + " path)" : "Chưa nạp. Chọn file .schema tải từ router để dùng trang Data Model."), file));
}

/* ---------- khung chính ---------- */
const EXTRA = [{ id: "log", title: "Log", type: "log" }, { id: "connect", title: "Kết nối", type: "connect" }];
function renderNav() {
  const nav = $("#nav"), cur = location.hash.slice(1) || "fleet";
  nav.replaceChildren(...[...PAGES, ...EXTRA].map(p => h("a", { class: p.id === cur ? "on" : "", href: "#" + p.id }, p.title)));
}
function route() {
  clearTimers(); renderNav();
  const id = location.hash.slice(1) || "fleet";
  const page = [...PAGES, ...EXTRA].find(p => p.id === id) || PAGES[0];
  const root = $("#view"); root.replaceChildren();
  ({ fleet: viewFleet, dashboard: viewDashboard, sections: p => viewSections(page, p), datamodel: viewDataModel, log: viewLog, connect: viewConnect })[page.type](root);
}
async function refreshState() {
  try {
    STATE = await api("/api/state");
    const p = $("#pill");
    p.textContent = STATE.status; p.className = "pill " + (STATE.connected ? "ok" : "err");
    $("#agent").textContent = STATE.devices + " router";
    $("#demo").style.display = STATE.demo ? "" : "none";
  } catch (e) { $("#pill").textContent = "Mất kết nối tới webapp"; $("#pill").className = "pill err"; }
}
(async function init() {
  PAGES = await api("/api/pages");
  await refreshState();
  await refreshDevices();
  $("#dev").addEventListener("change", e => { CUR = e.target.value; localStorage.setItem("dev", CUR); route(); });
  window.addEventListener("hashchange", route);
  setInterval(refreshState, 3000);
  setInterval(refreshDevices, 5000);
  route();
})();
</script></body></html>
"""


def main():
    global APP
    ap = argparse.ArgumentParser(description="USP/TR-369 web controller")
    ap.add_argument("--host", default="127.0.0.1", help="địa chỉ lắng nghe (mặc định chỉ máy này)")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--password", default="", help="mật khẩu truy cập webapp (khuyên dùng khi mở ra mạng)")
    ap.add_argument("--demo", action="store_true", help="dữ liệu giả, không cần router/MQTT")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    if not HAVE_USP and not args.demo:
        print(f"[!] Thiếu thư viện hoặc file: {IMPORT_ERR}\n"
              "    Cài: pip install paho-mqtt protobuf ; đặt usp_msg_1_3_pb2.py, usp_record_1_3_pb2.py cùng thư mục.\n"
              "    Hoặc chạy thử giao diện: python usp_web.py --demo")
        raise SystemExit(1)
    if args.host not in ("127.0.0.1", "localhost") and not args.password:
        print("[!] Cảnh báo: đang mở ra mạng mà không có --password; ai cũng có thể đổi cấu hình router.")

    APP = App(args.demo, args.password)
    if args.host in ("127.0.0.1", "localhost"):
        APP.allowed_hosts = {f"127.0.0.1:{args.port}", f"localhost:{args.port}"}
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '') else args.host}:{args.port}/"
    print(f"USP Web đang chạy: {url}  {'(DEMO)' if args.demo else ''}   Ctrl+C để dừng")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        APP.usp.disconnect()
        srv.server_close()


if __name__ == "__main__":
    main()
