from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),

    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="emailassistant/login.html"
        ),
        name="login",
    ),

    path("logout/", auth_views.LogoutView.as_view(), name="logout"),

    # Load all emailassistant application URLs
    path("", include("emailassistant.urls")),

]