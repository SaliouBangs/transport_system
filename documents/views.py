from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from utilisateurs.permissions import role_required

from .forms import DocumentForm
from .models import Document


def liste_documents(request):
    documents = Document.objects.select_related("camion")
    today = timezone.localdate()
    return render(
        request,
        "documents/documents.html",
        {"documents": documents, "today": today},
    )


def ajouter_document(request):
    if request.method == "POST":
        form = DocumentForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("documents")
    else:
        form = DocumentForm()

    return render(request, "documents/ajouter_document.html", {"form": form})


def modifier_document(request, id):
    document = get_object_or_404(Document, id=id)
    if request.method == "POST":
        form = DocumentForm(request.POST, instance=document)
        if form.is_valid():
            form.save()
            return redirect("documents")
    else:
        form = DocumentForm(instance=document)

    return render(
        request,
        "documents/modifier_document.html",
        {"form": form, "document": document},
    )


def supprimer_document(request, id):
    document = get_object_or_404(Document, id=id)
    document.delete()
    return redirect("documents")


liste_documents = role_required("logistique", "directeur")(liste_documents)
ajouter_document = role_required("logistique", "directeur")(ajouter_document)
modifier_document = role_required("logistique", "directeur")(modifier_document)
supprimer_document = role_required("logistique", "directeur")(supprimer_document)
