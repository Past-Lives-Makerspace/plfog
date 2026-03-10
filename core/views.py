from django.http import JsonResponse
from django.shortcuts import redirect, render


def health_check(request):
    return JsonResponse({"status": "ok"})


def home(request):
    if request.user.is_authenticated:
        return redirect("hub_guild_voting")
    return render(request, "home.html")
