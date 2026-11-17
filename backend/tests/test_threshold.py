import io, os, sys
APP=os.environ["FT_APP_DIR"]; FAKE=os.environ["FT_FAKE_DIR"]; MONGO=os.environ["FT_MONGO_URI"]; OUTSIDE=os.path.join(os.environ["FT_TMP_DIR"],"outside.jpg")
sys.dont_write_bytecode = True
sys.path.insert(0, APP); sys.path.insert(0, FAKE)
os.environ["MONGO_URI"]=MONGO
for k in ("FACE_MATCH_THRESHOLD","FACE_MODEL","ADMIN_API_KEY","SECRET_KEY","REQUIRE_LIVE_CAPTURE","ANTI_SPOOFING"): os.environ.pop(k,None)
from pymongo import MongoClient
MongoClient(MONGO).drop_database("attendance_system")
import glob
for f in glob.glob(APP+"/uploads/faces/*"): os.remove(f)
import app as appmod, face_settings
from deepface import DeepFace
from database.mongodb import db
from werkzeug.security import generate_password_hash
c=appmod.app.test_client(); L={"REMOTE_ADDR":"127.0.0.1"}; H=lambda t:{"Authorization":f"Bearer {t}"}

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
ok=fail=0
def check(n,cond,extra=""):
    global ok,fail
    if cond: ok+=1; print("PASS",n)
    else: fail+=1; print("FAIL",n,extra)
db.faculty.insert_one({"faculty_id":"F1","name":"P","email":"a","password":generate_password_hash("pw",method="pbkdf2:sha256")})
db.faculty_assignments.insert_one({"faculty_id":"F1","department_code":"IS","class_name":"3IS-A","subject_code":"DBMS"})
db.students.insert_many([{"usn":f"1BM23IS00{i}","name":f"S{i}","department_code":"IS","class_name":"3IS-A"} for i in (1,2)])
t=c.post("/faculty/login",json={"faculty_id":"F1","password":"pw"}).json["token"]
code=c.post("/enrollment/create",json={"class_name":"3IS-A"},headers=H(t),environ_base=L).json["code"]
for u in ("1BM23IS001","1BM23IS002"):
    c.post("/face/register",data={"usn":u,"enrollment_code":code,"image":(io.BytesIO(b"REGISTERED-"+u.encode()),"a.jpg")},content_type="multipart/form-data",environ_base=L)
def checkin(usn, selfie):
    sid=c.post("/session/create",json={"department_code":"IS","class_name":"3IS-A","subject_code":"DBMS"},headers=H(t),environ_base=L).json["session_id"]
    tok=c.get(f"/session/{sid}/qr",headers=H(t),environ_base=L).json["token"]
    p=c.post("/attendance/pass",json={"session_id":sid,"token":tok},environ_base=L).json["pass"]
    return c.post("/attendance/mark",data={"session_id":sid,"usn":usn,"pass":p,"capture_mode":"live","image":(io.BytesIO(selfie),"s.jpg")},content_type="multipart/form-data",environ_base=L), sid
print("== defaults")
check("default model Facenet512, library pass mark (None passed), effective 0.30, review flag at 0.255", face_settings.face_model()=="Facenet512" and face_settings.match_threshold() is None and face_settings.effective_threshold()==0.30 and face_settings.review_distance()==0.255)
r,sid=checkin("1BM23IS001",b"NEAR-genuine-like"); a=DeepFace.last_verify
check("genuine-like selfie (0.27) is accepted", r.status_code==200, r.data)
check("  ...DeepFace was asked to use Facenet512 / cosine / library threshold", a=={"model_name":"Facenet512","distance_metric":"cosine","threshold":None}, a)
r,_=checkin("1BM23IS002",b"FAR-stranger-like")
check("stranger-like selfie (0.43, like the wrong face seen in testing) is REJECTED (403)", r.status_code==403 and r.json["verified"] is False, r.data)
check("  ...and no attendance record exists for it", db.attendance_records.count_documents({"usn":"1BM23IS002"})==0)
rows={s["usn"]:s for s in c.get(f"/attendance/final/{sid}",headers=H(t),environ_base=L).json["students"]}
check("faculty sheet: a 0.27 match is flagged Borderline (review point 0.255) and held, not counted", rows["1BM23IS001"]["flags"]==["Held for review (not counted)","Borderline match"] and rows["1BM23IS001"]["status"]=="Absent", rows["1BM23IS001"])
print("== borderline matches are held for the faculty")
def live(sid): return c.get(f"/attendance/live/{sid}",headers=H(t),environ_base=L).json
def final(sid): return c.get(f"/attendance/final/{sid}",headers=H(t),environ_base=L).json
def review(sid,usn,rejected): return c.post(f"/attendance/reject/{sid}/{usn}",json={"rejected":rejected},headers=H(t),environ_base=L)
def mark_in(sid,usn,selfie):
    tok=c.get(f"/session/{sid}/qr",headers=H(t),environ_base=L).json["token"]; p=c.post("/attendance/pass",json={"session_id":sid,"token":tok},environ_base=L).json["pass"]
    return c.post("/attendance/mark",data={"session_id":sid,"usn":usn,"pass":p,"capture_mode":"live","image":(io.BytesIO(selfie),"s.jpg")},content_type="multipart/form-data",environ_base=L)
r,sidH=checkin("1BM23IS001",b"NEAR-look-alike")
check("a borderline match (0.27) is accepted as 'held' with an honest message", r.status_code==200 and r.json["held"] is True and "faculty will confirm" in r.json["message"], r.data)
check("  ...stored as status Review, selfie kept for the faculty", db.attendance_records.find_one({"session_id":sidH,"usn":"1BM23IS001"})["status"]=="Review" and db.attendance_records.find_one({"session_id":sidH,"usn":"1BM23IS001"})["selfie"])
lv=live(sidH); item=lv["check_ins"][0]
check("live list: NOT counted present, shown as 1 to review with the flags", lv["present"]==0 and lv["held"]==1 and item["held"] is True and item["flags"][0]=="Needs your review" and "Borderline match" in item["flags"], lv)
fs=final(sidH); check("final sheet: absent until confirmed (present count 0)", fs["present"]==0 and fs["absent"]==2, fs)
r=mark_in(sidH,"1BM23IS001",b"NEAR-look-alike"); check("trying again says it is waiting for the faculty (400)", r.status_code==400 and "waiting for your faculty" in r.json["message"], r.data)
r=review(sidH,"1BM23IS001",False); lv=live(sidH)
check("faculty Confirm -> Present, counted, no longer held", r.status_code==200 and r.json["status"]=="Present" and lv["present"]==1 and lv["held"]==0 and lv["check_ins"][0]["held"] is False and "Needs your review" not in lv["check_ins"][0]["flags"], (r.data,lv))
check("  ...final sheet now counts it (Borderline flag stays for the record)", final(sidH)["present"]==1 and "Borderline match" in {x["usn"]:x for x in final(sidH)["students"]}["1BM23IS001"]["flags"])
r,sidR=checkin("1BM23IS002",b"NEAR-look-alike-2"); review(sidR,"1BM23IS002",True)
check("faculty Reject of a held check-in -> Rejected, never counted", db.attendance_records.find_one({"session_id":sidR,"usn":"1BM23IS002"})["status"]=="Rejected" and final(sidR)["present"]==0)
r=mark_in(sidR,"1BM23IS002",b"NEAR-look-alike-2"); check("  ...and the student is told to speak to the faculty (403)", r.status_code==403 and "not accepted" in r.json["message"], r.data)
r,sidG=checkin("1BM23IS001",b"REGISTERED-1BM23IS001"); check("a clear match (0.10) is Present straight away, not held", r.status_code==200 and r.json["held"] is False and live(sidG)["present"]==1 and live(sidG)["held"]==0, r.data)
os.environ["HOLD_BORDERLINE"]="0"
r,sidO=checkin("1BM23IS002",b"NEAR-look-alike-3"); check("HOLD_BORDERLINE=0 turns it off: counted present, still flagged Borderline", r.status_code==200 and r.json["held"] is False and live(sidO)["present"]==1 and "Borderline match" in live(sidO)["check_ins"][0]["flags"], r.data)
os.environ.pop("HOLD_BORDERLINE")
check("hold_borderline() defaults on", face_settings.hold_borderline() is True)
print("== configuration")
os.environ["FACE_MATCH_THRESHOLD"]="0.45"
r,_=checkin("1BM23IS002",b"FAR-stranger-like"); check("FACE_MATCH_THRESHOLD=0.45 lets 0.43 through (operator's choice)", r.status_code==200 and DeepFace.last_verify["threshold"]==0.45, r.data)
check("  ...review flag moves with it (0.383)", face_settings.review_distance()==0.383)
os.environ["FACE_MATCH_THRESHOLD"]="0.2"
r,_=checkin("1BM23IS001",b"NEAR-genuine-like"); check("FACE_MATCH_THRESHOLD=0.2 rejects 0.27", r.status_code in (403,400), r.data)
os.environ["FACE_MATCH_THRESHOLD"]="garbage"; check("invalid value falls back to the model default", face_settings.match_threshold() is None)
os.environ.pop("FACE_MATCH_THRESHOLD")
os.environ["FACE_MODEL"]="VGG-Face"
check("VGG-Face keeps its stricter 0.50 (DeepFace's 0.68 let a stranger through)", face_settings.match_threshold()==0.5 and face_settings.review_distance()==0.425)
sid2=c.post("/session/create",json={"department_code":"IS","class_name":"3IS-A","subject_code":"DBMS"},headers=H(t),environ_base=L).json["session_id"]
tok=c.get(f"/session/{sid2}/qr",headers=H(t),environ_base=L).json["token"]; p=c.post("/attendance/pass",json={"session_id":sid2,"token":tok},environ_base=L).json["pass"]
c.post("/attendance/mark",data={"session_id":sid2,"usn":"1BM23IS001","pass":p,"image":(io.BytesIO(b"NEAR-x"),"s.jpg")},content_type="multipart/form-data",environ_base=L)
check("  ...and the model name is passed through", DeepFace.last_verify["model_name"]=="VGG-Face" and DeepFace.last_verify["threshold"]==0.5, DeepFace.last_verify)
os.environ.pop("FACE_MODEL")
print("== the public test route uses the same rule")
r=c.post("/face/verify",data={"usn":"1BM23IS002","image":(io.BytesIO(b"FAR-stranger-like"),"s.jpg")},content_type="multipart/form-data",environ_base=L,headers=H(t))
check("/face/verify rejects the 0.43 stranger too", r.json["verified"] is False, r.json)
print(f"\n{ok} passed, {fail} failed"); sys.exit(1 if fail else 0)
