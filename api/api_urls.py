from django.urls import path, include
from rest_framework.routers import DefaultRouter

from products.views import (
    CategoryViewSet,
    WarehouseViewSet,
    ProductViewSet,
    BundleItemViewSet,
    InventoryViewSet,
    InventoryTransferViewSet,
    IntegrationViewSet,
)
from orders.views import OrderViewSet
from shipments.views import ShipmentViewSet
from product_requests.views import RequestViewSet
from invitations.views import InvitationViewSet

router = DefaultRouter()

# Products app
router.register(r'categories', CategoryViewSet, basename='category')
router.register(r'warehouses', WarehouseViewSet, basename='warehouse')
router.register(r'products', ProductViewSet, basename='product')
router.register(r'kit-items', BundleItemViewSet, basename='kit-item')
router.register(r'inventory', InventoryViewSet, basename='inventory')
router.register(r'transfers', InventoryTransferViewSet, basename='transfer')
router.register(r'integrations', IntegrationViewSet, basename='integration')

# Other apps
router.register(r'orders', OrderViewSet, basename='order')
router.register(r'shipments', ShipmentViewSet, basename='shipment')
router.register(r'requests', RequestViewSet, basename='request')
router.register(r'invitations', InvitationViewSet, basename='invitation')

urlpatterns = [
    path('', include(router.urls)),
]
