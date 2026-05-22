from django.test import Client

response = Client().get("/media/depenses/justificatifs/hand-drawn-fake-autograph-sample-editable-stroke-signature-vector.jpg")
print(response.status_code)
print(response.get("Content-Type"))
