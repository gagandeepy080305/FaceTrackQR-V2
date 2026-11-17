class DeepFace:
    last_verify = {}
    @staticmethod
    def extract_faces(img_path, enforce_detection=True, anti_spoofing=False):
        data = open(img_path, "rb").read()
        if data == b"NOFACE":
            raise ValueError("Face could not be detected")
        face = {"facial_area": {"x": 200, "y": 200, "w": 300, "h": 300}}
        if anti_spoofing:
            face["is_real"] = data != b"SPOOF"; face["antispoof_score"] = 0.9
        return [face]
    @staticmethod
    def verify(img1_path, img2_path, model_name="VGG-Face", distance_metric="cosine", enforce_detection=True, threshold=None):
        DeepFace.last_verify = {"model_name": model_name, "distance_metric": distance_metric, "threshold": threshold}
        a = open(img1_path, "rb").read(); b = open(img2_path, "rb").read()
        if b == b"BAD": raise ValueError("Exception while processing img2_path")
        if enforce_detection and b == b"NOFACE": raise ValueError("Exception while processing img2_path")
        # distances on the Facenet512 scale (library pass mark 0.30)
        distance = 0.1 if a == b else 0.27 if b.startswith(b"NEAR") else 0.43 if b.startswith(b"FAR") else 0.9
        limit = threshold if threshold is not None else {"Facenet512": 0.30}.get(model_name, 0.68)
        return {"verified": distance <= limit, "distance": distance}
