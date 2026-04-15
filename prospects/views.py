from django.shortcuts import render, redirect, get_object_or_404
from django import forms
from .models import Prospect
from clients.models import Client
from utilisateurs.models import journaliser_action
from utilisateurs.permissions import role_required


# FORMULAIRE
class ProspectForm(forms.ModelForm):

    class Meta:
        model = Prospect
        fields = ['nom', 'telephone', 'entreprise', 'ville']

    def clean_entreprise(self):
        entreprise = (self.cleaned_data.get("entreprise") or "").strip()
        queryset = Prospect.objects.filter(entreprise__iexact=entreprise)
        if self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)
        if entreprise and queryset.exists():
            raise forms.ValidationError("Cette entreprise existe deja dans les prospects.")
        return entreprise


# LISTE PROSPECTS
def liste_prospects(request):

    prospects = Prospect.objects.all()

    return render(request, "prospects/prospects.html", {"prospects": prospects})


# AJOUTER PROSPECT
def ajouter_prospect(request):

    if request.method == "POST":

        form = ProspectForm(request.POST)

        if form.is_valid():
            prospect = form.save()
            journaliser_action(
                request.user,
                "Prospects",
                "Ajout de prospect",
                prospect.entreprise,
                f"{request.user.username} a ajoute le prospect {prospect.entreprise}.",
            )
            return redirect('prospects')

    else:
        form = ProspectForm()

    return render(request, "prospects/ajouter_prospect.html", {"form": form})


# CONVERTIR EN CLIENT
def convertir_client(request, id):

    prospect = get_object_or_404(Prospect, id=id)
    prospect_label = prospect.entreprise

    client, created = Client.objects.get_or_create(

        entreprise=prospect.entreprise,

        defaults={
            "nom": prospect.nom,
            "telephone": prospect.telephone,
            "ville": prospect.ville
        }

    )

    prospect.delete()
    journaliser_action(
        request.user,
        "Prospects",
        "Conversion en client",
        prospect_label,
        f"{request.user.username} a converti le prospect {prospect_label} en client.",
    )

    return redirect('prospects')


liste_prospects = role_required("commercial", "directeur")(liste_prospects)
ajouter_prospect = role_required("commercial", "directeur")(ajouter_prospect)
convertir_client = role_required("directeur")(convertir_client)
