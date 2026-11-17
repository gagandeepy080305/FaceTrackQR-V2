def find_threshold(model_name, metric):
    return {"VGG-Face": 0.68, "Facenet512": 0.30, "ArcFace": 0.68}[model_name]
