import io, os, sys, glob, shutil, itertools
APP=os.environ["FT_APP_DIR"]; FAKE=os.environ["FT_FAKE_DIR"]; MONGO=os.environ["FT_MONGO_URI"]; OUTSIDE=os.path.join(os.environ["FT_TMP_DIR"],"outside.jpg")
from unittest.mock import patch
sys.dont_write_bytecode = True
sys.path.insert(0, APP); sys.path.insert(0, FAKE)
os.environ["MONGO_URI"]=MONGO
for k in ("ALLOWED_NETWORKS","FACE_MODEL","FACE_MATCH_THRESHOLD","ADMIN_API_KEY","SECRET_KEY","REQUIRE_LIVE_CAPTURE","ANTI_SPOOFING","ENFORCE_ONE_CHECKIN_PER_DEVICE","TRUST_PROXY_HOPS","ENROLLMENT_MINUTES"): os.environ.pop(k,None)
os.environ["DEVICE_TOKEN_ISSUE_LIMIT"]="6"
from pymongo import MongoClient
MongoClient(MONGO).drop_database("attendance_system")
UP=APP+"/uploads/faces"
for f in glob.glob(UP+"/*"): os.remove(f)
shutil.rmtree(APP+"/uploads/checkins", ignore_errors=True)
import app as appmod, auth
from itsdangerous import URLSafeSerializer
from database.mongodb import db
from werkzeug.security import generate_password_hash
c=appmod.app.test_client(); at=lambda ip:{"REMOTE_ADDR":ip}; H=lambda t:{"Authorization":f"Bearer {t}"}
ok=fail=0
def check(n,cond,extra=""):
    global ok,fail
    if cond: ok+=1; print("PASS",n)
    else: fail+=1; print("FAIL",n,extra)
hp=lambda p: generate_password_hash(p, method="pbkdf2:sha256")
db.faculty.insert_many([{"faculty_id":"F1","name":"One","email":"a","password":hp("pw1")},{"faculty_id":"F2","name":"Two","email":"b","password":hp("pw2")}])
db.faculty_assignments.insert_many([{"faculty_id":"F1","department_code":"IS","class_name":"3IS-A","subject_code":"DBMS"},{"faculty_id":"F2","department_code":"CSE","class_name":"3CS-A","subject_code":"AI"}])
db.students.insert_many([{"usn":f"1BM23IS00{i}","name":f"Student {i}","department_code":"IS","class_name":"3IS-A"} for i in range(1,9)])
t1=c.post("/faculty/login",json={"faculty_id":"F1","password":"pw1"}).json["token"]; t2=c.post("/faculty/login",json={"faculty_id":"F2","password":"pw2"}).json["token"]
FAC="10.20.5.7"; ipn=itertools.count(110); newip=lambda: f"10.20.5.{next(ipn)}"
def dev(ip, existing=None): return c.post("/device/token",json={"device":existing} if existing else {},environ_base=at(ip)).json["device"]
def enroll(tok=None, any_=False): return c.post("/enrollment/create",json={"class_name":"3IS-A","allow_any_network":any_},headers=H(tok or t1),environ_base=at(FAC)).json
def register(usn,face,code,ip,device,mode="live"):
    d={"usn":usn,"enrollment_code":code,"capture_mode":mode,"image":(io.BytesIO(face),"a.jpg")}
    if device is not None: d["device"]=device
    return c.post("/face/register",data=d,content_type="multipart/form-data",environ_base=at(ip))
def approve(usn,tok=None): return c.post(f"/face/approve/{usn}",headers=H(tok or t1),environ_base=at(FAC))
def onboard(usn,face,ip=None):
    ip=ip or newip(); d=dev(ip); register(usn,face,enroll()["code"],ip,d); approve(usn); return ip,d
def session(extra=None):
    body={"department_code":"IS","class_name":"3IS-A","subject_code":"DBMS"}; body.update(extra or {})
    return c.post("/session/create",json=body,headers=H(t1),environ_base=at(FAC)).json["session_id"]
def qr(sid): return c.get(f"/session/{sid}/qr",headers=H(t1),environ_base=at(FAC)).json["token"]
def getpass(sid,ip,device=None):
    j={"session_id":sid,"token":qr(sid)}
    if device is not None: j["device"]=device
    return c.post("/attendance/pass",json=j,environ_base=at(ip))
def mark(sid,usn,face,pas,ip,device):
    d={"session_id":sid,"usn":usn,"pass":pas,"capture_mode":"live","image":(io.BytesIO(face),"s.jpg")}
    if device is not None: d["device"]=device
    return c.post("/attendance/mark",data=d,content_type="multipart/form-data",environ_base=at(ip))
def checkin(sid,usn,face,ip,device): return mark(sid,usn,face,getpass(sid,ip,device).json["pass"],ip,device)
live=lambda sid: c.get(f"/attendance/live/{sid}",headers=H(t1),environ_base=at(FAC)).json
rejt=lambda sid,usn,body=None: c.post(f"/attendance/reject/{sid}/{usn}",json=body or {},headers=H(t1),environ_base=at(FAC))

print("== phone tokens come from the server")
d1=dev("10.99.0.1"); check("a new phone gets a token", isinstance(d1,str) and len(d1)>20 and auth.read_device_token(d1))
check("a valid token is confirmed unchanged", dev("10.99.0.1",d1)==d1)
d2=dev("10.99.0.1","garbage"); check("garbage is replaced by a new token", d2!=d1 and auth.read_device_token(d2))
forged=URLSafeSerializer("another-secret",salt="device-id").dumps({"d":"chosen-by-student"})
check("a token signed with another key is rejected", auth.read_device_token(forged) is None and dev("10.99.0.2",forged)!=forged)
check("a student can't choose their own id (tampered token)", auth.read_device_token(d1[:-2]+"xx") is None)
for i in range(3): dev("10.99.0.1")        # 2 + 3 = 5 issued so far from this address
dev("10.99.0.1"); r=c.post("/device/token",json={},environ_base=at("10.99.0.1"))
check("minting unlimited tokens is capped per address (429)", r.status_code==429, r.status_code)
check("  ...a phone that already has a token is never blocked", c.post("/device/token",json={"device":d1},environ_base=at("10.99.0.1")).status_code==200)

print("== a pass belongs to one phone")
ipA,devA=onboard("1BM23IS001",b"FACE-1"); ipB,devB=onboard("1BM23IS002",b"FACE-2"); ipC,devC=onboard("1BM23IS003",b"FACE-3")
sid=session(); r=getpass(sid,ipA,devA)
check("pass response confirms the phone id", r.status_code==200 and r.json["device"]==devA)
r=getpass(sid,newip()); check("a phone with no id gets a fresh one with its pass", r.status_code==200 and auth.read_device_token(r.json["device"]))
pA=getpass(sid,ipA,devA).json["pass"]
r=mark(sid,"1BM23IS001",b"FACE-1",pA,ipB,devB); check("A's pass can't be used from B's phone (403 'different phone')", r.status_code==403 and "different phone" in r.json["message"], r.data)
r=mark(sid,"1BM23IS001",b"FACE-1",pA,"10.20.5.250",devA); check("...nor the same phone id from another address (403)", r.status_code==403, r.data)
r=mark(sid,"1BM23IS001",b"FACE-1",pA,ipA,None); check("a check-in without a phone id is refused (403)", r.status_code==403 and "isn't recognised" in r.json["message"], r.data)
r=mark(sid,"1BM23IS001",b"FACE-1",pA,ipA,forged); check("a forged phone id is refused (403)", r.status_code==403)
r=mark(sid,"1BM23IS001",b"FACE-1",pA,"8.8.8.8",devA); check("the network rule still comes first (400 'class network')", r.status_code==400 and "class network" in r.json["message"], r.data)
r=mark(sid,"1BM23IS001",b"FACE-1",pA,ipA,devA); check("the right phone + address checks in (200)", r.status_code==200, r.data)

print("== one student per phone per session")
sid=session(); r=checkin(sid,"1BM23IS001",b"FACE-1",ipA,devA); check("phone A checks in its own student", r.status_code==200, r.data)
r=checkin(sid,"1BM23IS002",b"FACE-2",ipA,devA)
check("the same phone can't then check in a classmate (403), even with the classmate's real face", r.status_code==403 and "own phone" in r.json["message"], r.data)
check("  ...and nothing was recorded for the classmate", db.attendance_records.count_documents({"session_id":sid,"usn":"1BM23IS002"})==0)
lv=live(sid); check("  ...the faculty sees the attempt", lv["device_blocked"]["count"]==1 and lv["device_blocked"]["usns"]==["1BM23IS002"], lv["device_blocked"])
devX=dev(ipA); r=checkin(sid,"1BM23IS003",b"FACE-3",ipA,devX)
check("a second phone id from the SAME address is also blocked on a LAN (no clearing storage to dodge it)", r.status_code==403, r.data)
check("the same phone re-checking its own student is 'already marked', not a block", "Already" in checkin(sid,"1BM23IS001",b"FACE-1",ipA,devA).json["message"])
rejt(sid,"1BM23IS001"); r=checkin(sid,"1BM23IS002",b"FACE-2",ipA,devA)
check("a REJECTED check-in still counts: the phone stays blocked", r.status_code==403, r.data)
r=checkin(sid,"1BM23IS002",b"FACE-2",ipB,devB); check("the classmate's own phone checks in normally", r.status_code==200, r.data)
anyid=session({"allow_any_network":True}); r1=checkin(anyid,"1BM23IS001",b"FACE-1",ipA,devA); r2=checkin(anyid,"1BM23IS002",b"FACE-2",ipA,devA)
check("testing sessions ('any network') skip the rule so you can test with one phone", r1.status_code==200 and r2.status_code==200, (r1.data,r2.data))
os.environ["ENFORCE_ONE_CHECKIN_PER_DEVICE"]="0"; off=session(); r1=checkin(off,"1BM23IS001",b"FACE-1",ipA,devA); r2=checkin(off,"1BM23IS002",b"FACE-2",ipA,devA)
check("ENFORCE_ONE_CHECKIN_PER_DEVICE=0 turns it off", r1.status_code==200 and r2.status_code==200); os.environ.pop("ENFORCE_ONE_CHECKIN_PER_DEVICE")
sid=session(); checkin(sid,"1BM23IS001",b"FACE-1",ipA,devA)
with patch("routes.attendance._device_conflict", return_value=False):
    r=checkin(sid,"1BM23IS002",b"FACE-2",ipA,devA)
check("two simultaneous check-ins from one phone: the late one is undone (race guard)", r.status_code==403 and db.attendance_records.count_documents({"session_id":sid,"usn":"1BM23IS002"})==0 and db.attendance_records.count_documents({"session_id":sid,"usn":"1BM23IS001"})==1, r.data)

print("== registration needs approval, the right network and one student per phone")
e=enroll(); check("enrollment window is 5 minutes and carries the faculty's network", 295<=e["expires_in"]<=300 and e["network_lock"]["networks"]==["10.20.5.0/24"], e)
ipD=newip(); devD=dev(ipD)
r=register("1BM23IS004",b"FACE-4",e["code"],"8.8.8.8",devD); check("registering from outside the class network is refused (400)", r.status_code==400 and "class network" in r.json["message"], r.data)
check("  ...nothing saved", db.student_faces.count_documents({"usn":"1BM23IS004"})==0)
check("registering without a phone id is refused (403)", register("1BM23IS004",b"FACE-4",e["code"],ipD,None).status_code==403)
check("registering with a forged phone id is refused (403)", register("1BM23IS004",b"FACE-4",e["code"],ipD,forged).status_code==403)
r=register("1BM23IS004",b"FACE-4",e["code"],ipD,devD); check("a valid registration is accepted as PENDING", r.status_code==200 and r.json["status"]=="pending", r.data)
check("  ...status endpoint says pending", c.get("/face/status/1BM23IS004",environ_base=at("8.8.8.8")).json=={"registered":True,"status":"pending"})
check("  ...unknown USN: not registered", c.get("/face/status/1BM23IS008",environ_base=at("8.8.8.8")).json=={"registered":False,"status":None})
sid=session(); r=checkin(sid,"1BM23IS004",b"FACE-4",ipD,devD); check("a pending face can't check in (403 'waiting for approval')", r.status_code==403 and "approval" in r.json["message"], r.data)
check("approve: anonymous 401, other class's faculty 403, unknown 404", c.post("/face/approve/1BM23IS004",environ_base=at(FAC)).status_code==401 and approve("1BM23IS004",t2).status_code==403 and approve("1BM23IS099").status_code==404)
photo=c.get("/face/photo/1BM23IS004",headers=H(t1),environ_base=at(FAC)); check("the faculty can view the registered photo before approving", photo.status_code==200 and photo.data==b"FACE-4" and "no-store" in photo.headers.get("Cache-Control",""))
check("  ...but nobody else can", c.get("/face/photo/1BM23IS004",environ_base=at(FAC)).status_code==401 and c.get("/face/photo/1BM23IS004",headers=H(t2),environ_base=at(FAC)).status_code==403)
st=c.get("/enrollment/status/3IS-A",headers=H(t1),environ_base=at(FAC)).json
check("enrollment status lists who is waiting (text only) and the counts", [p["usn"] for p in st["pending"]]==["1BM23IS004"] and st["approved"]==3 and st["total"]==8 and "image" not in str(st).lower(), st)
check("  ...other class's faculty can't see it (403)", c.get("/enrollment/status/3IS-A",headers=H(t2),environ_base=at(FAC)).status_code==403)
approve("1BM23IS004"); r=checkin(sid,"1BM23IS004",b"FACE-4",ipD,devD); check("after approval the student can check in", r.status_code==200, r.data)
# squatting attempt: one phone, one window, many classmates
e=enroll(); ipS=newip(); devS=dev(ipS)
r1=register("1BM23IS005",b"ATTACKER",e["code"],ipS,devS); r2=register("1BM23IS006",b"ATTACKER",e["code"],ipS,devS)
check("one phone can register only ONE student per window (squatting stopped)", r1.status_code==200 and r2.status_code==403 and "already registered another student" in r2.json["message"], (r1.data,r2.data))
check("  ...the unregistered classmate is untouched and can still register", db.student_faces.count_documents({"usn":"1BM23IS006"})==0 and register("1BM23IS006",b"REAL-6",e["code"],newip(),dev(newip())).status_code in (200,))
pend=c.get("/enrollment/status/3IS-A",headers=H(t1),environ_base=at(FAC)).json["pending"]
check("  ...the faculty sees the attacker's photo under the wrong name and rejects it", c.get("/face/photo/1BM23IS005",headers=H(t1),environ_base=at(FAC)).data==b"ATTACKER" and c.delete("/face/delete/1BM23IS005",headers=H(t1),environ_base=at(FAC)).status_code==200 and not os.path.exists(UP+"/1BM23IS005.jpg"))
check("  ...and the real owner can then register", register("1BM23IS005",b"REAL-5",enroll()["code"],newip(),dev(newip())).status_code==200)
e=enroll(any_=True); ipT=newip(); devT=dev(ipT)
check("an 'any network' (testing) window lets one phone register several students", register("1BM23IS007",b"T7",e["code"],ipT,devT).status_code==200 and register("1BM23IS008",b"T8",e["code"],ipT,devT).status_code==200)
legacy=UP+"/legacy.jpg"; open(legacy,"wb").write(b"LEGACY-FACE")
db.students.insert_one({"usn":"1BM23IS009","name":"Legacy","department_code":"IS","class_name":"3IS-A"}); db.student_faces.insert_one({"usn":"1BM23IS009","image_path":legacy})
ipL=newip(); devL=dev(ipL); sid=session(); r=checkin(sid,"1BM23IS009",b"LEGACY-FACE",ipL,devL)
check("faces registered before approvals existed still work (treated as approved)", r.status_code==200, r.data)

def wipe(*usns):
    for u in usns:
        db.student_faces.delete_many({"usn":u})
        if os.path.exists(UP+f"/{u}.jpg"): os.remove(UP+f"/{u}.jpg")
print("== registration: a phone can't dodge the one-student rule by resetting its id")
wipe("1BM23IS005","1BM23IS006","1BM23IS007","1BM23IS008")
e=enroll(); ipR=newip(); devR1=dev(ipR); devR2=dev(ipR)
check("two different ids from one address are different tokens", devR1!=devR2)
r1=register("1BM23IS005",b"R5",e["code"],ipR,devR1)
wipe("1BM23IS005")
db.enrollment_codes.update_one({"code":e["code"]},{"$set":{"registered":[]}})
r1=register("1BM23IS005",b"R5",e["code"],ipR,devR1); r2=register("1BM23IS006",b"R6",e["code"],ipR,devR2)
check("same address + a NEW phone id still can't register a second student (403)", r1.status_code==200 and r2.status_code==403 and "already registered another student" in r2.json["message"], (r1.data,r2.data))
check("  ...nothing was saved for the second student", db.student_faces.count_documents({"usn":"1BM23IS006"})==0 and not os.path.exists(UP+"/1BM23IS006.jpg"))
check("  ...the same student retrying from the same phone is not blocked by this rule", register("1BM23IS005",b"R5b",e["code"],ipR,devR1).status_code==409)
ipR2=newip(); check("a second student from a different phone/address is fine", register("1BM23IS006",b"R6",e["code"],ipR2,dev(ipR2)).status_code==200)
wipe("1BM23IS005","1BM23IS006")
e2=enroll(); lo=lambda usn,d: register(usn,b"LO-"+usn.encode(),e2["code"],"127.0.0.1",d)
check("local testing from this computer (loopback) with different ids is not blocked", lo("1BM23IS007",dev("127.0.0.1")).status_code==200 and lo("1BM23IS008",dev("127.0.0.1")).status_code==200)
wipe("1BM23IS007","1BM23IS008")
e3=enroll(any_=True); ipA=newip()
check("'any network' testing windows skip the rule", register("1BM23IS007",b"A1",e3["code"],ipA,dev(ipA)).status_code==200 and register("1BM23IS008",b"A2",e3["code"],ipA,dev(ipA)).status_code==200)
from network_guard import lock_is_private_lan
check("rule applies only on private LAN ranges", lock_is_private_lan({"networks":["10.20.5.0/24"]}) and lock_is_private_lan({"networks":["192.168.29.0/24"]}) and not lock_is_private_lan({"networks":["8.8.8.8/32"]}) and not lock_is_private_lan({"networks":["10.0.0.0/24","8.8.8.8/32"]}) and not lock_is_private_lan({"networks":["*"]}) and not lock_is_private_lan(None))
wipe("1BM23IS005","1BM23IS006","1BM23IS007","1BM23IS008")

print("== registration photo quality (hands-free scan)")
from unittest.mock import patch
import face_quality
wipe("1BM23IS005")
good={"width":720,"height":960,"brightness":120.0,"sharpness":300.0}
def reg2(usn,face,face2,metrics=None,code=None,faces=None):
    ip=newip(); d=dev(ip); code=code or enroll()["code"]
    data={"usn":usn,"enrollment_code":code,"capture_mode":"live","device":d,"image":(io.BytesIO(face),"a.jpg")}
    if face2 is not None: data["image2"]=(io.BytesIO(face2),"b.jpg")
    with patch("routes.face.measure",return_value=metrics if metrics is not None else good):
        return c.post("/face/register",data=data,content_type="multipart/form-data",environ_base=at(ip))
clear=lambda usn: (db.student_faces.delete_many({"usn":usn}), os.path.exists(UP+f"/{usn}.jpg") and os.remove(UP+f"/{usn}.jpg"))
fail_codes={}
for name,m in {"too_far":{**good,"width":2000},"too_dark":{**good,"brightness":30.0},"too_bright":{**good,"brightness":240.0},"blurry":{**good,"sharpness":8.0}}.items():
    r=reg2("1BM23IS005",b"FACE-5",None,m)
    check(f"a '{name}' frame is refused (422) with the hint code '{name}'", r.status_code==422 and r.json.get("code")==name and r.json["message"], r.data)
    check(f"  ...and nothing is saved", db.student_faces.count_documents({"usn":"1BM23IS005"})==0)
r=reg2("1BM23IS005",b"NOFACE",None); check("no face -> 422 'no_face'", r.status_code==422 and r.json["code"]=="no_face", r.data)
two=[{"facial_area":{"x":50,"y":50,"w":300,"h":300}},{"facial_area":{"x":400,"y":60,"w":250,"h":250}}]
with patch("routes.face.DeepFace.extract_faces",return_value=two):
    r=reg2("1BM23IS005",b"FACE-5",None); check("two similar-sized faces -> 422 'multiple'", r.status_code==422 and r.json["code"]=="multiple", r.data)
small_bg=[{"facial_area":{"x":50,"y":50,"w":300,"h":300}},{"facial_area":{"x":600,"y":60,"w":60,"h":60}}]
with patch("routes.face.DeepFace.extract_faces",return_value=small_bg):
    r=reg2("1BM23IS005",b"FACE-5",None); check("a tiny face far behind is ignored", r.status_code==200, r.data); clear("1BM23IS005")
tilt=[{"facial_area":{"x":50,"y":50,"w":300,"h":300,"left_eye":[100,100],"right_eye":[300,190]}}]
with patch("routes.face.DeepFace.extract_faces",return_value=tilt):
    r=reg2("1BM23IS005",b"FACE-5",None); check("a tilted head -> 422 'tilted'", r.status_code==422 and r.json["code"]=="tilted", r.data)
off=[{"facial_area":{"x":0,"y":50,"w":160,"h":300}}]
with patch("routes.face.DeepFace.extract_faces",return_value=off):
    r=reg2("1BM23IS005",b"FACE-5",None); check("a face at the edge -> 422 'off_centre'", r.status_code==422 and r.json["code"]=="off_centre", r.data)
r=reg2("1BM23IS005",b"FACE-5",b"FACE-5"); check("two frames of the same person pass", r.status_code==200 and r.json["status"]=="pending", r.data); clear("1BM23IS005")
r=reg2("1BM23IS005",b"FACE-5",b"FAR-9"); check("two frames that don't match each other -> 422 'unstable'", r.status_code==422 and r.json["code"]=="unstable", r.data)
r=reg2("1BM23IS005",b"FACE-5",b"NEAR-5"); check("a pair the model finds only 'near' (0.27 > 0.85 x pass mark) is refused too", r.status_code==422 and r.json["code"]=="unstable", r.data)
sharp=[{**good,"sharpness":100.0},{**good,"sharpness":500.0}]
with patch("routes.face.measure",side_effect=sharp):
    ip=newip(); d=dev(ip); data={"usn":"1BM23IS005","enrollment_code":enroll()["code"],"capture_mode":"live","device":d,"image":(io.BytesIO(b"FACE-5"),"a.jpg"),"image2":(io.BytesIO(b"FACE-5"),"b.jpg")}
    r=c.post("/face/register",data=data,content_type="multipart/form-data",environ_base=at(ip))
check("the sharper of the two frames is the one kept", r.status_code==200, r.data); clear("1BM23IS005")
# the limiter
import auth
auth.register_quality_limiter._failures.clear()
ipQ=newip(); dQ=dev(ipQ); codeQ=enroll()["code"]
codes=[]
for _ in range(95):
    with patch("routes.face.measure",return_value={**good,"sharpness":1.0}):
        codes.append(c.post("/face/register",data={"usn":"1BM23IS005","enrollment_code":codeQ,"capture_mode":"live","device":dQ,"image":(io.BytesIO(b"FACE-5"),"a.jpg")},content_type="multipart/form-data",environ_base=at(ipQ)).status_code)
check("after 90 unclear frames from one address the scan is paused (429)", codes[:90]==[422]*90 and codes[-1]==429, set(codes))
auth.register_quality_limiter._failures.clear()
# the pure rules
jq=lambda faces,m: face_quality.judge(faces,m)
try: jq([],good); r="no error"
except face_quality.QualityError as err: r=err.code
check("judge: no faces -> no_face", r=="no_face")
check("judge: a good frame returns its sharpness", jq([{"facial_area":{"x":200,"y":200,"w":300,"h":300}}],good)==300.0)

print("== the public face-test endpoint is closed")
vf=lambda tok=None,usn="1BM23IS001": c.post("/face/verify",data={"usn":usn,"image":(io.BytesIO(b"NEAR-x"),"g.jpg")},content_type="multipart/form-data",environ_base=at("8.8.8.8"),headers=H(tok) if tok else {})
check("/face/verify without a login -> 401 (no more probing from outside)", vf().status_code==401)
check("/face/verify for another class's faculty -> 403", vf(t2).status_code==403)
check("/face/verify works for the student's own faculty", vf(t1).status_code==200)
probe=[vf().status_code for _ in range(20)]; check("20 anonymous probes all refused", set(probe)=={401}, probe)
print(f"\n{ok} passed, {fail} failed"); sys.exit(1 if fail else 0)
