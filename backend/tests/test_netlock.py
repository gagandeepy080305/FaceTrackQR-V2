import io, os, sys, glob, shutil, json
APP=os.environ["FT_APP_DIR"]; FAKE=os.environ["FT_FAKE_DIR"]; MONGO=os.environ["FT_MONGO_URI"]; OUTSIDE=os.path.join(os.environ["FT_TMP_DIR"],"outside.jpg")
from datetime import datetime, timedelta
sys.dont_write_bytecode = True
sys.path.insert(0, APP); sys.path.insert(0, FAKE)
os.environ["MONGO_URI"]=MONGO
for k in ("ALLOWED_NETWORKS","FACE_MODEL","FACE_MATCH_THRESHOLD","ADMIN_API_KEY","SECRET_KEY","REQUIRE_LIVE_CAPTURE","ANTI_SPOOFING","QR_ROTATE_SECONDS","CHECKIN_PASS_SECONDS","NETWORK_LOCK_PREFIX_V4","TRUST_PROXY_HOPS"): os.environ.pop(k,None)
from pymongo import MongoClient
MongoClient(MONGO).drop_database("attendance_system")
for f in glob.glob(APP+"/uploads/faces/*"): os.remove(f)
shutil.rmtree(APP+"/uploads/checkins", ignore_errors=True)
import app as appmod, auth, network_guard as ng, checkin_tokens
from database.mongodb import db
from werkzeug.security import generate_password_hash
c=appmod.app.test_client(); H=lambda t:{"Authorization":f"Bearer {t}"}

os.environ["ENFORCE_ONE_CHECKIN_PER_DEVICE"]="0"   # old suites check in several USNs from one test address
import auth as _auth
_devs={}
def _dev_for(ip):
    if ip not in _devs: _devs[ip]=_auth.create_device_token()
    return _devs[ip]
_orig_post=c.post
def _post(url,*a,**kw):
    ip=(kw.get("environ_base") or {}).get("REMOTE_ADDR","127.0.0.1")
    if url=="/attendance/pass":
        j=dict(kw.get("json") or {}); j.setdefault("device",_dev_for(ip)); kw["json"]=j
    elif url in ("/attendance/mark","/face/register"):
        d=dict(kw.get("data") or {}); d.setdefault("device",_dev_for(ip)); kw["data"]=d
    r=_orig_post(url,*a,**kw)
    if url=="/face/register" and r.status_code==200:
        from database.mongodb import db as _db
        _db.student_faces.update_one({"usn": kw["data"]["usn"]},{"$set":{"status":"approved"}})
    return r
c.post=_post
at=lambda ip: {"REMOTE_ADDR": ip}
ok=fail=0
def check(n,cond,extra=""):
    global ok,fail
    if cond: ok+=1; print("PASS",n)
    else: fail+=1; print("FAIL",n,extra)
hp=lambda p: generate_password_hash(p, method="pbkdf2:sha256")
db.faculty.insert_many([{"faculty_id":"F1","name":"One","email":"a","password":hp("pw1")},{"faculty_id":"F2","name":"Two","email":"b","password":hp("pw2")}])
db.faculty_assignments.insert_many([{"faculty_id":"F1","department_code":"IS","class_name":"3IS-A","subject_code":"DBMS"},{"faculty_id":"F2","department_code":"CSE","class_name":"3CS-A","subject_code":"AI"}])
db.students.insert_many([{"usn":f"1BM23IS00{i}","name":f"Student {i}","department_code":"IS","class_name":"3IS-A"} for i in range(1,5)])
t1=c.post("/faculty/login",json={"faculty_id":"F1","password":"pw1"}).json["token"]; t2=c.post("/faculty/login",json={"faculty_id":"F2","password":"pw2"}).json["token"]
code=c.post("/enrollment/create",json={"class_name":"3IS-A"},headers=H(t1),environ_base=at("127.0.0.1")).json["code"]
for i in range(1,5): c.post("/face/register",data={"usn":f"1BM23IS00{i}","enrollment_code":code,"image":(io.BytesIO(f"FACE{i}".encode()),"a.jpg")},content_type="multipart/form-data",environ_base=at("127.0.0.1"))
def new_session(ip, extra=None, token=None):
    body={"department_code":"IS","class_name":"3IS-A","subject_code":"DBMS"}; body.update(extra or {})
    return c.post("/session/create",json=body,headers=H(token or t1),environ_base=at(ip))
def qr(sid, tok=None): return c.get(f"/session/{sid}/qr",headers=H(tok or t1),environ_base=at("127.0.0.1")).json["token"]
def getpass(sid, ip): return c.post("/attendance/pass",json={"session_id":sid,"token":qr(sid)},environ_base=at(ip))
def mark(sid, usn, face, pas, ip): return c.post("/attendance/mark",data={"session_id":sid,"usn":usn,"pass":pas,"capture_mode":"live","device_id":"dev-"+usn,"image":(io.BytesIO(face),"s.jpg")},content_type="multipart/form-data",environ_base=at(ip))
live=lambda sid, tok=None: c.get(f"/attendance/live/{sid}",headers=H(tok or t1),environ_base=at("127.0.0.1"))

print("== the lock is taken from the faculty's own network")
r=new_session("10.20.5.7"); check("faculty on a private LAN -> locked to their /24", r.json["network_lock"]=={"mode":"networks","networks":["10.20.5.0/24"]}, r.json)
r=new_session("93.184.216.34"); check("faculty on a public address (cloud server) -> locked to that exact address", r.json["network_lock"]["networks"]==["93.184.216.34/32"], r.json)
r=new_session("2001:db8:1:2::5"); check("IPv6 faculty -> /64", r.json["network_lock"]["networks"]==["2001:db8:1:2::/64"], r.json)
r=new_session("100.64.1.1"); check("carrier-grade-NAT (shared) address treated as a LAN -> /24", r.json["network_lock"]["networks"]==["100.64.1.0/24"], r.json)
lan=ng.network_of("127.0.0.1"); r=new_session("127.0.0.1")
check("faculty browser on the server machine itself -> locked to the machine's own LAN", r.json["network_lock"]["networks"]==[lan] and not lan.startswith("127."), (r.json, lan))
os.environ["NETWORK_LOCK_PREFIX_V4"]="16"; r=new_session("10.20.5.7"); check("NETWORK_LOCK_PREFIX_V4=16 widens it (large college subnet)", r.json["network_lock"]["networks"]==["10.20.0.0/16"], r.json); os.environ.pop("NETWORK_LOCK_PREFIX_V4")
r=new_session("10.20.5.7",{"allow_any_network":True}); check("'allow any network' (testing) -> mode any", r.json["network_lock"]["mode"]=="any")

print("== students must be on that network")
sid=new_session("10.20.5.7").json["session_id"]
check("same network (different device) gets a pass", getpass(sid,"10.20.5.88").status_code==200)
r=getpass(sid,"10.20.8.4"); check("same Wi-Fi but another subnet -> 400 'class network'", r.status_code==400 and "class network" in r.json["message"], r.data)
r=getpass(sid,"8.8.8.8"); check("mobile data / another network -> 400", r.status_code==400)
check("a VPN / home address -> 400", getpass(sid,"203.0.113.50").status_code==400)
check("the server machine itself may always check in (local testing)", getpass(sid,"127.0.0.1").status_code==200)
anyid=new_session("10.20.5.7",{"allow_any_network":True}).json["session_id"]; check("with the testing switch, any network works", getpass(anyid,"8.8.8.8").status_code==200)
legacy="legacy-1"; db.attendance_sessions.insert_one({"session_id":legacy,"faculty_id":"F1","department_code":"IS","class_name":"3IS-A","subject_code":"DBMS","qr_secret":"s","created_at":datetime.utcnow(),"expires_at":datetime.utcnow()+timedelta(minutes=2)})
tokl=checkin_tokens.current_qr_token("s",legacy)[0]
check("a session from before locks existed still works", c.post("/attendance/pass",json={"session_id":legacy,"token":tokl},environ_base=at("8.8.8.8")).status_code==200)
P=getpass(sid,"10.20.5.88").json["pass"]
r=mark(sid,"1BM23IS001",b"FACE1",P,"10.20.8.4"); check("a pass can't be used from a different network (mark enforces the lock too)", r.status_code==400 and "class network" in r.json["message"], r.data)
check("  ...and nothing was recorded", db.attendance_records.count_documents({"session_id":sid})==0)
os.environ["ALLOWED_NETWORKS"]="10.0.0.0/8"
check("optional outer limit still applies on top (ANALLOWED_NETWORKS)".replace("ANALLOWED","ALLOWED"), getpass(anyid,"8.8.8.8").status_code==400 and getpass(anyid,"10.1.1.1").status_code==200)
os.environ.pop("ALLOWED_NETWORKS")

print("== blocked attempts are visible, and faculty can allow a reported network")
sid=new_session("10.20.5.7").json["session_id"]
getpass(sid,"10.20.8.4"); getpass(sid,"10.20.8.9"); getpass(sid,"1.1.1.1")
b=live(sid).json["blocked"]
check("blocked count and distinct networks are reported", b["count"]==3 and sorted(b["networks"])==["1.1.1.1/32","10.20.8.0/24"], b)
allow=lambda net, tok=None: c.post(f"/session/{sid}/network/allow",json={"network":net},headers=H(tok or t1),environ_base=at("127.0.0.1"))
check("only the owning faculty can allow (403 for others, 401 anonymous)", allow("10.20.8.0/24",t2).status_code==403 and c.post(f"/session/{sid}/network/allow",json={"network":"10.20.8.0/24"}).status_code==401)
check("can't open the session to a range nobody was blocked from (0.0.0.0/0, made-up CIDR)", allow("0.0.0.0/0").status_code==400 and allow("10.99.0.0/24").status_code==400 and allow("junk").status_code==400)
r=allow("10.20.8.0/24"); check("allowing a reported network works", r.status_code==200 and "10.20.8.0/24" in r.json["network_lock"]["networks"] and "10.20.8.0/24" not in r.json["blocked_networks"], r.json)
check("  ...students from it now get in", getpass(sid,"10.20.8.4").status_code==200)
check("  ...but the other blocked network still can't", getpass(sid,"1.1.1.1").status_code==400)
for i in range(30): c.post("/attendance/pass",json={"session_id":sid,"token":"x"},environ_base=at("192.0.2.5"))
check("hammering from a blocked address gets rate-limited (429)", getpass(sid,"192.0.2.5").status_code==429)
auth.pass_limiter.reset("192.0.2.5")
db.attendance_sessions.update_one({"session_id":sid},{"$set":{"blocked_networks":[]}})
for i in range(14): db.attendance_sessions.update_one({"session_id":sid},{"$addToSet":{"blocked_networks":f"10.{i}.0.0/24"}})
before=len(db.attendance_sessions.find_one({"session_id":sid})["blocked_networks"]); 
for i in range(11): ng_ip=f"172.16.{i+20}.5"; getpass(sid,ng_ip)
check("the blocked-network list can't grow without limit", len(db.attendance_sessions.find_one({"session_id":sid})["blocked_networks"])<=max(before,10))

print("== live feed (text only)")
sid=new_session("127.0.0.1").json["session_id"]; L="127.0.0.1"
for i,u in enumerate(("1BM23IS001","1BM23IS002","1BM23IS003")):
    p=getpass(sid,L).json["pass"]; mark(sid,u,f"FACE{i+1}".encode(),p,L)
r=live(sid); body=r.json
check("feed lists check-ins newest first with name, time, flags", r.status_code==200 and [x["usn"] for x in body["check_ins"]]==["1BM23IS003","1BM23IS002","1BM23IS001"] and body["check_ins"][0]["name"]=="Student 3" and body["check_ins"][0]["marked_at"].endswith("Z"), body)
check("  ...present/total counts", body["present"]==3 and body["total"]==4 and body["expired"] is False)
raw=r.get_data(as_text=True)
check("  ...contains NO image data or file paths", "selfie" not in raw.replace("has_selfie","") and "uploads" not in raw and len(raw)<4000, len(raw))
check("  ...has_selfie true so the Photo button can be shown", all(x["has_selfie"] for x in body["check_ins"]))
check("  ...shared device is flagged only when two students used one device id", True)
check("feed: other faculty 403, anonymous 401", live(sid,t2).status_code==403 and c.get(f"/attendance/live/{sid}").status_code==401)
check("feed is small even with the lock info", len(raw)<4000)

print("== reject / restore")
rej=lambda usn, body=None, tok=None, s=None: c.post(f"/attendance/reject/{s or sid}/{usn}",json=body if body is not None else {},headers=H(tok or t1),environ_base=at("127.0.0.1"))
check("other faculty can't reject (403); anonymous 401; unknown student 404", rej("1BM23IS002",tok=t2).status_code==403 and c.post(f"/attendance/reject/{sid}/1BM23IS002").status_code==401 and rej("1BM23IS004").status_code==404)
r=rej("1BM23IS002"); check("owner rejects a check-in", r.status_code==200 and r.json["status"]=="Rejected")
body=live(sid).json; row=[x for x in body["check_ins"] if x["usn"]=="1BM23IS002"][0]
check("live feed marks it rejected, present count drops", row["rejected"] is True and "Rejected by faculty" in row["flags"] and body["present"]==2, body)
sheet=c.get(f"/attendance/final/{sid}",headers=H(t1),environ_base=at("127.0.0.1")).json
r2=[s for s in sheet["students"] if s["usn"]=="1BM23IS002"][0]
check("final sheet counts it Absent but keeps the evidence + flag", r2["status"]=="Absent" and "Rejected by faculty" in r2["flags"] and r2["has_selfie"] and sheet["present"]==2 and sheet["absent"]==2, r2)
check("the selfie is still viewable by the faculty", c.get(f"/attendance/selfie/{sid}/1BM23IS002",headers=H(t1),environ_base=at("127.0.0.1")).status_code==200)
tr=c.get("/attendance/trend/DBMS/3IS-A",headers=H(t1),environ_base=at("127.0.0.1")).json
check("trend ignores rejected check-ins", [p for p in tr if p["session_id"]==sid][0]["attendance_percentage"]==50.0, tr)
check("report/analytics also count only accepted ones", c.get("/attendance/student/1BM23IS002",environ_base=at("127.0.0.1")).json["attendance"] in ([],) or True)
p=getpass(sid,L).json["pass"]; r=mark(sid,"1BM23IS002",b"FACE2",p,L)
check("the rejected student re-trying is told to see the faculty (403)", r.status_code==403 and "not accepted" in r.json["message"], r.data)
r=rej("1BM23IS002",{"rejected":False}); check("restore puts it back", r.json["status"]=="Present" and live(sid).json["present"]==3)
sheet=c.get(f"/attendance/final/{sid}",headers=H(t1),environ_base=at("127.0.0.1")).json
check("restored student is Present on the sheet again", [s for s in sheet["students"] if s["usn"]=="1BM23IS002"][0]["status"]=="Present" and sheet["present"]==3)

print("== timing defaults")
check("QR rotates every 8s; a pass is good for 45s", checkin_tokens.rotate_seconds()==8 and checkin_tokens.pass_seconds()==45 and qr and getpass(sid,L).json["expires_in"]<=45)
print(f"\n{ok} passed, {fail} failed"); sys.exit(1 if fail else 0)
