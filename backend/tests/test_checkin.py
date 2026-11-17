import io, os, sys, time, uuid, glob
APP=os.environ["FT_APP_DIR"]; FAKE=os.environ["FT_FAKE_DIR"]; MONGO=os.environ["FT_MONGO_URI"]; OUTSIDE=os.path.join(os.environ["FT_TMP_DIR"],"outside.jpg")
from datetime import datetime, timedelta
from unittest.mock import patch
sys.dont_write_bytecode = True
sys.path.insert(0, APP); sys.path.insert(0, FAKE)
os.environ["MONGO_URI"]=MONGO
for k in ("ADMIN_API_KEY","SECRET_KEY","REQUIRE_LIVE_CAPTURE","ANTI_SPOOFING","QR_ROTATE_SECONDS","CHECKIN_PHOTO_DAYS","CHECKIN_PASS_SECONDS"): os.environ.pop(k, None)
from pymongo import MongoClient
MongoClient(MONGO).drop_database("attendance_system")
import app as appmod, auth, checkin_tokens, evidence
from database.mongodb import db
from werkzeug.security import generate_password_hash
c = appmod.app.test_client(); LOCAL={"REMOTE_ADDR":"127.0.0.1"}

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
UPF=os.path.join(APP,"uploads","faces"); CK=evidence.CHECKIN_FOLDER
for f in glob.glob(UPF+"/*"): os.remove(f)
import shutil; shutil.rmtree(CK, ignore_errors=True)
ok=fail=0
def check(name, cond, extra=""):
    global ok, fail
    if cond: ok+=1; print("PASS", name)
    else: fail+=1; print("FAIL", name, extra)
def H(t): return {"Authorization": f"Bearer {t}"}
hp=lambda p: generate_password_hash(p, method="pbkdf2:sha256")

db.faculty.insert_many([{"faculty_id":"F1","name":"One","email":"a","password":hp("pw1")},{"faculty_id":"F2","name":"Two","email":"b","password":hp("pw2")}])
db.faculty_assignments.insert_many([{"faculty_id":"F1","department_code":"IS","class_name":"3IS-A","subject_code":"DBMS"},{"faculty_id":"F2","department_code":"CSE","class_name":"3CS-A","subject_code":"AI"}])
db.students.insert_many([{"usn":f"1BM23IS00{i}","name":f"S{i}","department_code":"IS","class_name":"3IS-A"} for i in range(1,7)])
t1=c.post("/faculty/login",json={"faculty_id":"F1","password":"pw1"}).json["token"]; t2=c.post("/faculty/login",json={"faculty_id":"F2","password":"pw2"}).json["token"]
def newsession(): return c.post("/session/create",json={"department_code":"IS","class_name":"3IS-A","subject_code":"DBMS"},headers=H(t1),environ_base=LOCAL).json["session_id"]
def qr(sid,tok=t1): return c.get(f"/session/{sid}/qr",headers=H(tok),environ_base=LOCAL)
def getpass(sid, token=None, ip="127.0.0.1"):
    token = token or qr(sid).json["token"]
    return c.post("/attendance/pass",json={"session_id":sid,"token":token},environ_base={"REMOTE_ADDR":ip})
def mark(usn, face, sid, pas, extra=None, ip="127.0.0.1"):
    d={"session_id":sid,"usn":usn,"image":(io.BytesIO(face),"s.jpg")}
    if pas is not None: d["pass"]=pas
    d.update(extra or {})
    return c.post("/attendance/mark",data=d,content_type="multipart/form-data",environ_base={"REMOTE_ADDR":ip})
def reg(usn, face):
    code=c.post("/enrollment/create",json={"class_name":"3IS-A"},headers=H(t1),environ_base=LOCAL).json["code"]
    return c.post("/face/register",data={"usn":usn,"enrollment_code":code,"image":(io.BytesIO(face),"a.jpg")},content_type="multipart/form-data",environ_base=LOCAL)
for i in range(1,5): reg(f"1BM23IS00{i}", f"FACE{i}".encode())

print("\n== QR token endpoint")
sid=newsession()
check("anonymous -> 401", c.get(f"/session/{sid}/qr",environ_base=LOCAL).status_code==401)
check("other faculty -> 403", qr(sid,t2).status_code==403)
check("unknown session -> 404", qr("nope").status_code==404)
r=qr(sid); tok=r.json["token"]
check("owner gets a 16-char token, rotate 8s, 1..8s left", r.status_code==200 and len(tok)==16 and r.json["rotate_seconds"]==8 and 1<=r.json["expires_in"]<=8, r.data)
check("same window -> same token", qr(sid).json["token"]==tok)
check("secret never leaks (create/history/final/qr responses)", all("qr_secret" not in c.get(u,headers=H(t1),environ_base=LOCAL).get_data(as_text=True) for u in [f"/session/history/F1",f"/attendance/final/{sid}",f"/session/{sid}/qr"]))
base=time.time()
with patch("checkin_tokens._now", return_value=base+9):
    check("next window -> a different token", qr(sid).json["token"]!=tok)
db.attendance_sessions.update_one({"session_id":sid},{"$set":{"expires_at":datetime.utcnow()-timedelta(seconds=1)}})
check("expired session -> 410", qr(sid).status_code==410)
legacy=str(uuid.uuid4()); db.attendance_sessions.insert_one({"session_id":legacy,"faculty_id":"F1","department_code":"IS","class_name":"3IS-A","subject_code":"DBMS","created_at":datetime.utcnow(),"expires_at":datetime.utcnow()+timedelta(minutes=2)})
check("session created before this feature -> 409 (no secret)", qr(legacy).status_code==409)

print("\n== trading a QR token for a pass")
sid=newsession()
W=(int(time.time()//8)+10)*8          # a window boundary well in the future (deterministic)
with patch("checkin_tokens._now", return_value=W+1): tok=qr(sid).json["token"]
with patch("checkin_tokens._now", return_value=W+2): r=getpass(sid,tok)
check("fresh token -> pass issued, valid <=45s", r.status_code==200 and 20<=r.json["expires_in"]<=45 and len(r.json["pass"])>30, r.data)
with patch("checkin_tokens._now", return_value=W+9): r=getpass(sid,tok)
check("token one window old is still accepted (slow scan)", r.status_code==200, r.data)
with patch("checkin_tokens._now", return_value=W+17): r=getpass(sid,tok)
check("token two windows old is rejected (shared screenshot) -> 403", r.status_code==403 and "expired" in r.json["message"], r.data)
check("made-up token -> 403", getpass(sid,"0123456789abcdef").status_code==403)
check("token from ANOTHER session -> 403", getpass(sid,qr(newsession()).json["token"]).status_code==403)
check("missing fields -> 400", c.post("/attendance/pass",json={"session_id":sid},environ_base=LOCAL).status_code==400)
check("invalid session -> 400", c.post("/attendance/pass",json={"session_id":"x","token":"y"},environ_base=LOCAL).status_code==400)
check("a different network than the session -> 400 class-network message", (lambda r: r.status_code==400 and "class network" in r.json["message"])(getpass(sid,tok,ip="8.8.8.8")))
short=newsession(); stok=qr(short).json["token"]
db.attendance_sessions.update_one({"session_id":short},{"$set":{"expires_at":datetime.utcnow()+timedelta(seconds=9)}})
r=getpass(short,stok); check("pass lifetime is capped by the time the session has left", r.status_code==200 and r.json["expires_in"]<=9, r.json)
db.attendance_sessions.update_one({"session_id":short},{"$set":{"expires_at":datetime.utcnow()-timedelta(seconds=1)}})
check("expired session -> no pass (400)", getpass(short,stok).status_code==400)
for i in range(30): getpass(sid,"badbadbadbadbad%d"%(i%10),ip="10.5.5.5") if False else c.post("/attendance/pass",json={"session_id":sid,"token":"bad%d"%i},environ_base={"REMOTE_ADDR":"127.0.0.1"})
check("30 bad tokens lock the address (429)", getpass(sid,tok).status_code==429)
auth.pass_limiter.reset("127.0.0.1")

print("\n== check-in requires (and burns) a pass")
sid=newsession(); P=getpass(sid).json["pass"]
check("no pass -> 403", mark("1BM23IS001",b"FACE1",sid,None).status_code==403)
check("garbage pass -> 403", mark("1BM23IS001",b"FACE1",sid,"garbage").status_code==403)
other=newsession(); Po=getpass(other).json["pass"]
check("pass for a different session -> 403", (lambda r: r.status_code==403 and "different session" in r.json["message"])(mark("1BM23IS001",b"FACE1",sid,Po)))
with patch("itsdangerous.timed.time") as m:
    m.time.return_value=time.time()+1000
    check("expired pass -> 403", mark("1BM23IS001",b"FACE1",sid,P).status_code==403)
r=mark("1BM23IS001",b"WRONG",sid,P); check("wrong face -> 403 and pass NOT burned", r.status_code==403 and db.used_passes.count_documents({})==0, r.data)
r=mark("1BM23IS001",b"FACE1",sid,P,{"capture_mode":"live","device_id":"dev-AAA bad!chars"}); check("retry with the right face on the same pass -> success", r.status_code==200, r.data)
check("pass is now burned", db.used_passes.count_documents({})==1)
r=mark("1BM23IS002",b"FACE2",sid,P); check("same pass can't check in another student (409)", r.status_code==409 and "already used" in r.json["message"], r.data)
P2=getpass(sid).json["pass"]
r=mark("1BM23IS001",b"FACE1",sid,P2); check("same student again -> Already Marked (new pass)", r.status_code==400 and "Already" in r.json["message"], r.data)

print("\n== live-capture requirement (opt-in)")
os.environ["REQUIRE_LIVE_CAPTURE"]="1"
sid2=newsession()
r=mark("1BM23IS003",b"FACE3",sid2,getpass(sid2).json["pass"]); check("file upload refused when REQUIRE_LIVE_CAPTURE=1 (400)", r.status_code==400 and "Live camera" in r.json["message"], r.data)
r=mark("1BM23IS003",b"FACE3",sid2,getpass(sid2).json["pass"],{"capture_mode":"file"}); check("  ...explicit file mode too", r.status_code==400)
r=mark("1BM23IS003",b"FACE3",sid2,getpass(sid2).json["pass"],{"capture_mode":"live"}); check("live capture accepted", r.status_code==200, r.data)
os.environ.pop("REQUIRE_LIVE_CAPTURE")

print("\n== evidence")
rec=db.attendance_records.find_one({"session_id":sid,"usn":"1BM23IS001"})
check("record keeps ip, distance, mode, the SERVER-issued device id (client-sent id ignored), user agent", rec["client_ip"]=="127.0.0.1" and rec["distance"]==0.1 and rec["capture_mode"]=="live" and rec["device_id"]==_auth.read_device_token(_dev_for("127.0.0.1")) and "user_agent" in rec, rec)
f=f"{CK}/{sid}/1BM23IS001.jpg"
check("selfie stored on disk, byte-for-byte", os.path.exists(f) and open(f,"rb").read()==b"FACE1")
sheet=c.get(f"/attendance/final/{sid}",headers=H(t1),environ_base=LOCAL).json
row=[s for s in sheet["students"] if s["usn"]=="1BM23IS001"][0]
check("faculty sheet row: time, match score, selfie flag, no warnings", row["status"]=="Present" and row["marked_at"].endswith("Z") and row["distance"]==0.1 and row["has_selfie"] is True and row["flags"]==[], row)
check("absent rows stay minimal", [s for s in sheet["students"] if s["usn"]=="1BM23IS004"][0]=={"usn":"1BM23IS004","name":"S4","status":"Absent"})
# flags
sid3=newsession()
mark("1BM23IS001",b"FACE1",sid3,getpass(sid3).json["pass"],{"capture_mode":"live","device_id":"phone-1"})
mark("1BM23IS002",b"FACE2",sid3,getpass(sid3).json["pass"],{"capture_mode":"file","device_id":"phone-1"})
mark("1BM23IS003",b"FACE3",sid3,getpass(sid3).json["pass"],{"capture_mode":"live","device_id":"phone-3"})
db.attendance_records.update_one({"session_id":sid3,"usn":"1BM23IS003"},{"$set":{"distance":0.55}})
rows={s["usn"]:s for s in c.get(f"/attendance/final/{sid3}",headers=H(t1),environ_base=LOCAL).json["students"]}
check("same device used by two students -> both flagged 'Shared device'", "Shared device" in rows["1BM23IS001"]["flags"] and "Shared device" in rows["1BM23IS002"]["flags"], rows)
check("file upload -> 'Not live camera'", "Not live camera" in rows["1BM23IS002"]["flags"])
check("distance >= 0.5 -> 'Borderline match'", "Borderline match" in rows["1BM23IS003"]["flags"], rows["1BM23IS003"])
# selfie endpoint
check("selfie: anonymous 401", c.get(f"/attendance/selfie/{sid}/1BM23IS001",environ_base=LOCAL).status_code==401)
check("selfie: other faculty 403", c.get(f"/attendance/selfie/{sid}/1BM23IS001",headers=H(t2),environ_base=LOCAL).status_code==403)
r=c.get(f"/attendance/selfie/{sid}/1bm23is001",headers=H(t1),environ_base=LOCAL)
check("selfie: owner gets the image, not cacheable", r.status_code==200 and r.data==b"FACE1" and "no-store" in r.headers.get("Cache-Control","") and r.mimetype=="image/jpeg", (r.status_code, r.headers.get("Cache-Control")))
check("selfie: no check-in -> 404", c.get(f"/attendance/selfie/{sid}/1BM23IS004",headers=H(t1),environ_base=LOCAL).status_code==404)
db.attendance_records.update_one({"session_id":sid,"usn":"1BM23IS001"},{"$set":{"selfie":"../../../../etc/hosts"}})
check("selfie: path traversal in a record is refused (404)", c.get(f"/attendance/selfie/{sid}/1BM23IS001",headers=H(t1),environ_base=LOCAL).status_code==404)
db.attendance_records.update_one({"session_id":sid,"usn":"1BM23IS001"},{"$set":{"selfie":f"{sid}/1BM23IS001.jpg"}})
# retention
old_sid=str(uuid.uuid4()); os.makedirs(f"{CK}/{old_sid}",exist_ok=True); open(f"{CK}/{old_sid}/1BM23IS001.jpg","wb").write(b"OLD")
db.attendance_records.insert_one({"session_id":old_sid,"usn":"1BM23IS001","marked_at":datetime.utcnow()-timedelta(days=40),"selfie":f"{old_sid}/1BM23IS001.jpg","status":"Present"})
db.used_passes.insert_one({"_id":"stale","created_at":datetime.utcnow()-timedelta(hours=3)})
os.environ["CHECKIN_PHOTO_DAYS"]="0"; evidence.purge_old_evidence()
check("CHECKIN_PHOTO_DAYS=0 keeps photos forever", os.path.exists(f"{CK}/{old_sid}/1BM23IS001.jpg"))
os.environ.pop("CHECKIN_PHOTO_DAYS"); evidence.purge_old_evidence()
check("photos older than 30 days are deleted (file + field)", not os.path.exists(f"{CK}/{old_sid}/1BM23IS001.jpg") and "selfie" not in db.attendance_records.find_one({"session_id":old_sid}))
check("recent photos are kept", os.path.exists(f"{CK}/{sid}/1BM23IS001.jpg"))
check("stale used-pass markers are cleaned up", db.used_passes.count_documents({"_id":"stale"})==0)

print("\n== anti-spoofing (opt-in, selfie only)")
check("registering a print-like photo works while spoof checking is off", reg("1BM23IS005",b"SPOOF").status_code==200)
sid4=newsession()
r=mark("1BM23IS005",b"SPOOF",sid4,getpass(sid4).json["pass"]); check("off by default: nothing changes (check-in works)", r.status_code==200, r.data)
os.environ["ANTI_SPOOFING"]="1"
db.attendance_records.delete_many({"session_id":sid4}); 
r=mark("1BM23IS005",b"SPOOF",sid4,getpass(sid4).json["pass"]); check("ANTI_SPOOFING=1: spoofed selfie rejected (422)", r.status_code==422 and "screen or a print" in r.json["message"], r.data)
check("  ...no record, pass not burned", db.attendance_records.count_documents({"session_id":sid4,"usn":"1BM23IS005"})==0)
check("  ...registration of a print-like photo is NOT blocked (selfie-only rule)", reg("1BM23IS006",b"SPOOF").status_code==200)
r=mark("1BM23IS003",b"FACE3",sid4,getpass(sid4).json["pass"]); check("  ...genuine selfies still pass", r.status_code==200, r.data)
os.environ.pop("ANTI_SPOOFING")
print(f"\n{ok} passed, {fail} failed"); sys.exit(1 if fail else 0)
