"""
URL configuration for api project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.views import APIView
from rest_framework.response import Response

class HealthCheckView(APIView):
    """Health check endpoint"""
    permission_classes = []

    def get(self, request):
        return Response({'status': 'healthy', 'message': 'Omni Link API is running'})

urlpatterns = [
    path('admin/', admin.site.urls),

    # Health check
    path('api/health/', HealthCheckView.as_view(), name='health-check'),

    # API Documentation
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),

    # Authentication endpoints
    path('api/auth/', include('authentication.urls')),

    # API endpoints
    path('api/', include('api.api_urls')),
]




