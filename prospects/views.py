from django.shortcuts import render, redirect, get_object_or_404
from django import forms
from django.contrib.auth.models import User
from .models import Prospect
from clients.models import Client
from utilisateurs.models import journaliser_action
from utilisateurs.permissions import get_user_role, is_admin_user, role_required
from utilisateurs.constants import ROLE_COMMERCIAL, ROLE_RESPONSABLE_COMMERCIAL


# FORMULAIRE
class ProspectForm(forms.ModelForm):
    commercial = forms.ModelChoiceField(
        queryset=User.objects.none(),
        required=False,
        label="Commercial responsable",
    )

    class Meta:
        model = Prospect
        fields = ['commercial', 'nom', 'fonction', 'telephone', 'entreprise', 'ville']

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        self.fields["commercial"].queryset = User.objects.filter(
            groups__name__in=[ROLE_COMMERCIAL, ROLE_RESPONSABLE_COMMERCIAL]
        ).exclude(is_superuser=True).distinct().order_by("first_name", "last_name", "username")

        role = get_user_role(user) if user else ""
        if role == ROLE_COMMERCIAL:
            self.fields["commercial"].initial = user
            self.fields["commercial"].widget = forms.HiddenInput()
            self.fields["commercial"].required = False

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
    prospects = Prospect.objects.select_related("commercial")
    if get_user_role(request.user) == ROLE_COMMERCIAL and not is_admin_user(request.user):
        prospects = prospects.filter(commercial=request.user)

    return render(request, "prospects/prospects.html", {"prospects": prospects})


# AJOUTER PROSPECT
def ajouter_prospect(request):

    if request.method == "POST":
        form = ProspectForm(request.POST, user=request.user)

        if form.is_valid():
            prospect = form.save(commit=False)
            if get_user_role(request.user) == ROLE_COMMERCIAL and not is_admin_user(request.user):
                prospect.commercial = request.user
            prospect.save()
            journaliser_action(
                request.user,
                "Prospects",
                "Ajout de prospect",
                prospect.entreprise,
                f"{request.user.username} a ajoute le prospect {prospect.entreprise}.",
            )
            return redirect('prospects')

    else:
        form = ProspectForm(user=request.user)

    return render(request, "prospects/ajouter_prospect.html", {"form": form})


def modifier_prospect(request, id):
    prospects = Prospect.objects.all()
    if get_user_role(request.user) == ROLE_COMMERCIAL and not is_admin_user(request.user):
        prospects = prospects.filter(commercial=request.user)
    prospect = get_object_or_404(prospects, id=id)

    if request.method == "POST":
        form = ProspectForm(request.POST, instance=prospect, user=request.user)
        if form.is_valid():
            prospect = form.save(commit=False)
            if get_user_role(request.user) == ROLE_COMMERCIAL and not is_admin_user(request.user):
                prospect.commercial = request.user
            prospect.save()
            journaliser_action(
                request.user,
                "Prospects",
                "Modification de prospect",
                prospect.entreprise,
                f"{request.user.username} a modifie le prospect {prospect.entreprise}.",
            )
            return redirect("prospects")
    else:
        form = ProspectForm(instance=prospect, user=request.user)

    return render(request, "prospects/modifier_prospect.html", {"form": form, "prospect": prospect})


def supprimer_prospect(request, id):
    prospect = get_object_or_404(Prospect, id=id)
    prospect_label = prospect.entreprise
    prospect.delete()
    journaliser_action(
        request.user,
        "Prospects",
        "Suppression de prospect",
        prospect_label,
        f"{request.user.username} a supprime le prospect {prospect_label}.",
    )
    return redirect("prospects")


# CONVERTIR EN CLIENT
def convertir_client(request, id):
    prospects = Prospect.objects.all()
    if get_user_role(request.user) == ROLE_COMMERCIAL and not is_admin_user(request.user):
        prospects = prospects.filter(commercial=request.user)
    prospect = get_object_or_404(prospects, id=id)
    prospect_label = prospect.entreprise

    client, created = Client.objects.get_or_create(

        entreprise=prospect.entreprise,

        defaults={
            "nom": prospect.nom,
            "fonction_contact": prospect.fonction,
            "telephone": prospect.telephone,
            "ville": prospect.ville,
            "commercial": prospect.commercial or (request.user if get_user_role(request.user) in {"commercial", "responsable_commercial"} else None),
            "adresse": "",
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


liste_prospects = role_required("commercial", "responsable_commercial")(liste_prospects)
ajouter_prospect = role_required("commercial", "responsable_commercial")(ajouter_prospect)
modifier_prospect = role_required("commercial", "responsable_commercial")(modifier_prospect)
supprimer_prospect = role_required("directeur")(supprimer_prospect)
convertir_client = role_required("commercial", "responsable_commercial")(convertir_client)
