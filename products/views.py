"""
Products app views.

Design notes
────────────
* ModelViewSets for standard CRUD on Product, Warehouse, Category.
* Custom actions on WarehouseViewSet for per-warehouse inventory ops.
* A dedicated InventoryViewSet for global inventory queries.
* A dedicated TransferViewSet that delegates to the service layer.
* Every mutation that touches Inventory goes through services.py
  which wraps everything in atomic + select_for_update.
"""

from django.db.models import Sum, Count, F, Value
from django.db.models.functions import Coalesce
from rest_framework import viewsets, status, filters, mixins
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django_filters.rest_framework import DjangoFilterBackend
from authentication.permissions import IsAdminOrOwner

from .models import (
    Category,
    Warehouse,
    Product,
    ProductBundle,
    Inventory,
    InventoryTransfer,
    Market,
)
from .serializers import (
    CategorySerializer,
    WarehouseSerializer,
    ProductSerializer,
    BundleItemSerializer,
    AssembleBundleSerializer,
    DisassembleBundleSerializer,
    InventorySerializer,
    InventoryTransferSerializer,
    AddStockSerializer,
    RemoveStockSerializer,
    InventorySummarySerializer,
    MarketSerializer,
)
from . import services


# ---------------------------------------------------------------------------
# Market
# ---------------------------------------------------------------------------
class MarketViewSet(viewsets.ModelViewSet):
    """CRUD for markets."""
    queryset = Market.objects.all()
    serializer_class = MarketSerializer
    permission_classes = [IsAuthenticated, IsAdminOrOwner]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['is_active']
    search_fields = ['name', 'code']
    ordering_fields = ['name', 'code', 'created_at']
    ordering = ['name']


# ---------------------------------------------------------------------------
# Category
# ---------------------------------------------------------------------------
class CategoryViewSet(viewsets.ModelViewSet):
    """CRUD for product categories."""

    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name']
    ordering_fields = ['name', 'created_at']
    ordering = ['name']


# ---------------------------------------------------------------------------
# Warehouse
# ---------------------------------------------------------------------------
class WarehouseViewSet(viewsets.ModelViewSet):
    """
    CRUD for warehouses plus inventory helper actions:

    • GET  /warehouses/{id}/inventory/   → inventory for this warehouse
    • POST /warehouses/{id}/add_stock/   → add stock
    • POST /warehouses/{id}/remove_stock/ → remove stock
    """

    queryset = (
        Warehouse.objects
        .select_related('manager')
        .annotate(annotated_total_stock=Coalesce(Sum('inventory_items__quantity'), Value(0)))
    )
    serializer_class = WarehouseSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name', 'location']
    ordering_fields = ['name', 'created_at']
    ordering = ['-created_at']

    def perform_create(self, serializer):
        serializer.save(manager=serializer.validated_data.get('manager', self.request.user))

    # ---- custom actions ---------------------------------------------------

    @action(detail=True, methods=['get'])
    def inventory(self, request, pk=None):
        """List every product / quantity in this warehouse."""
        warehouse = self.get_object()
        qs = (
            Inventory.objects
            .filter(warehouse=warehouse)
            .select_related('product', 'warehouse')
        )
        page = self.paginate_queryset(qs)
        if page is not None:
            serializer = InventorySerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = InventorySerializer(qs, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'], url_path='add-stock')
    def add_stock(self, request, pk=None):
        """
        Add stock to this warehouse.

        Request body:
            { "product": <product_id>, "quantity": <int> }
        """
        warehouse = self.get_object()
        ser = AddStockSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        inv = services.add_stock(
            product=ser.validated_data['product'],
            warehouse=warehouse,
            quantity=ser.validated_data['quantity'],
            user=request.user,
        )
        return Response(
            InventorySerializer(inv).data,
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=['post'], url_path='remove-stock')
    def remove_stock(self, request, pk=None):
        """
        Remove stock from this warehouse.

        Request body:
            { "product": <product_id>, "quantity": <int> }
        """
        warehouse = self.get_object()
        ser = RemoveStockSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        inv = services.remove_stock(
            product=ser.validated_data['product'],
            warehouse=warehouse,
            quantity=ser.validated_data['quantity'],
            user=request.user,
        )
        return Response(
            InventorySerializer(inv).data,
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=['get'])
    def stats(self, request, pk=None):
        """Warehouse-level statistics."""
        warehouse = self.get_object()
        agg = (
            Inventory.objects
            .filter(warehouse=warehouse)
            .aggregate(
                total_stock=Sum('quantity'),
                total_reserved=Sum('reserved'),
                product_count=Count('product', distinct=True),
            )
        )
        total_value = (
            Inventory.objects
            .filter(warehouse=warehouse)
            .aggregate(
                value=Sum(F('quantity') * F('product__price'))
            )['value']
        ) or 0

        return Response({
            'warehouse_id': warehouse.id,
            'warehouse_name': warehouse.name,
            'product_count': agg['product_count'] or 0,
            'total_stock': agg['total_stock'] or 0,
            'total_reserved': agg['total_reserved'] or 0,
            'total_value': float(total_value),
            'capacity': warehouse.capacity,
            'utilization_pct': round(
                ((agg['total_stock'] or 0) / warehouse.capacity * 100), 2
            ) if warehouse.capacity > 0 else 0,
        })


# ---------------------------------------------------------------------------
# Product
# ---------------------------------------------------------------------------
class ProductViewSet(viewsets.ModelViewSet):
    """
    CRUD for products.

    Custom actions:
    • GET  /products/{id}/inventory/   → stock across all warehouses
    • POST /products/{id}/assemble/    → assemble bundle from components
    • POST /products/{id}/disassemble/ → break bundle back into components
    """

    queryset = (
        Product.objects
        .select_related('category')
        .prefetch_related('bundle_items__component')
        .annotate(annotated_total_stock=Coalesce(Sum('inventory_items__quantity'), Value(0)))
    )
    serializer_class = ProductSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['category', 'is_bundle']
    search_fields = ['name', 'sku', 'description']
    ordering_fields = ['name', 'price', 'created_at']
    ordering = ['-created_at']

    def get_queryset(self):
        queryset = super().get_queryset()
        is_kit = self.request.query_params.get('is_kit')
        if is_kit is not None:
            normalized = is_kit.strip().lower()
            if normalized in {'1', 'true', 'yes'}:
                queryset = queryset.filter(is_bundle=True)
            elif normalized in {'0', 'false', 'no'}:
                queryset = queryset.filter(is_bundle=False)
        return queryset

    @action(detail=True, methods=['get'])
    def inventory(self, request, pk=None):
        """Show this product's stock in every warehouse."""
        product = self.get_object()
        if not product.is_physical:
            page = self.paginate_queryset([])
            if page is not None:
                return self.get_paginated_response([])
            return Response([])

        qs = (
            Inventory.objects
            .filter(product=product)
            .select_related('product', 'warehouse')
        )
        page = self.paginate_queryset(qs)
        if page is not None:
            serializer = InventorySerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = InventorySerializer(qs, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def assemble(self, request, pk=None):
        """
        Assemble bundle units from component inventory in a warehouse.

        Consumes each component × quantity from the warehouse and adds
        the assembled bundle units to the same warehouse.

        Request body:
            { "warehouse": <id>, "quantity": <int> }
        """
        bundle = self.get_object()
        ser = AssembleBundleSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        bundle_inv = services.assemble_bundle(
            bundle=bundle,
            warehouse=ser.validated_data['warehouse'],
            quantity=ser.validated_data['quantity'],
            user=request.user,
        )
        return Response(
            InventorySerializer(bundle_inv).data,
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=['post'])
    def disassemble(self, request, pk=None):
        """
        Disassemble bundle units back into components.

        Removes bundle units from the warehouse and returns each component
        × quantity back to inventory.

        Request body:
            { "warehouse": <id>, "quantity": <int> }
        """
        bundle = self.get_object()
        ser = DisassembleBundleSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        component_inventories = services.disassemble_bundle(
            bundle=bundle,
            warehouse=ser.validated_data['warehouse'],
            quantity=ser.validated_data['quantity'],
            user=request.user,
        )
        return Response(
            InventorySerializer(component_inventories, many=True).data,
            status=status.HTTP_200_OK,
        )


# ---------------------------------------------------------------------------
# Bundle Items (CRUD for bundle components)
# ---------------------------------------------------------------------------
class BundleItemViewSet(viewsets.ModelViewSet):
    """
    Manage the bill-of-materials for product bundles.

    Supports filtering by ?bundle=<product_id>.
    """

    queryset = ProductBundle.objects.select_related('bundle', 'component').all()
    serializer_class = BundleItemSerializer
    permission_classes = [IsAuthenticated, IsAdminOrOwner]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['bundle', 'component']
    ordering = ['bundle', 'component']

    def get_queryset(self):
        queryset = super().get_queryset()
        kit = self.request.query_params.get('kit')
        if kit:
            queryset = queryset.filter(bundle_id=kit)
        return queryset


# ---------------------------------------------------------------------------
# Inventory (read-only + summary)
# ---------------------------------------------------------------------------
class InventoryViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """
    Read/update view over all Inventory rows.

    Supports filtering by ?product=<id> or ?warehouse=<id>.

    Custom action:
        GET /inventory/summary/ → per-product summary across warehouses
    """

    queryset = (
        Inventory.objects
        .select_related('product', 'warehouse')
        .all()
    )
    serializer_class = InventorySerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['product', 'warehouse']
    ordering_fields = ['quantity', 'updated_at']
    ordering = ['-updated_at']

    @action(detail=False, methods=['get'])
    def summary(self, request):
        """
        Aggregated inventory summary grouped by product.

        Response example:
        [
          {
            "product_id": 1,
            "product_name": "Widget",
            "product_sku": "WDG-001",
            "total_quantity": 500,
            "total_reserved": 20,
            "warehouse_count": 3,
            "needs_reorder": false
          }
        ]
        """
        qs = (
            Inventory.objects
            .values(
                'product__id',
                'product__name',
                'product__sku',
                'product__reorder_level',
            )
            .annotate(
                total_quantity=Sum('quantity'),
                total_reserved=Sum('reserved'),
                warehouse_count=Count('warehouse', distinct=True),
            )
            .order_by('product__name')
        )

        data = []
        for row in qs:
            data.append({
                'product_id': row['product__id'],
                'product_name': row['product__name'],
                'product_sku': row['product__sku'],
                'total_quantity': row['total_quantity'],
                'total_reserved': row['total_reserved'],
                'warehouse_count': row['warehouse_count'],
                'needs_reorder': (
                    row['total_quantity'] <= row['product__reorder_level']
                ),
            })

        page = self.paginate_queryset(data)
        if page is not None:
            serializer = InventorySummarySerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = InventorySummarySerializer(data, many=True)
        return Response(serializer.data)


# ---------------------------------------------------------------------------
# Inventory Transfer
# ---------------------------------------------------------------------------
class InventoryTransferViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """
    POST to create (and immediately execute) an inventory transfer.
    GET  to list / retrieve past transfers.

    Supports filtering by ?product=<id>, ?status=<STATUS>,
    ?from_warehouse=<id>, ?to_warehouse=<id>.
    """

    queryset = (
        InventoryTransfer.objects
        .select_related('product', 'from_warehouse', 'to_warehouse', 'initiated_by')
        .all()
    )
    serializer_class = InventoryTransferSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['product', 'status', 'from_warehouse', 'to_warehouse']
    ordering_fields = ['created_at', 'quantity']
    ordering = ['-created_at']

    def create(self, request, *args, **kwargs):
        """
        Create AND execute a transfer atomically.

        Request body:
        {
          "product": 1,
          "from_warehouse": 2,
          "to_warehouse": 3,
          "quantity": 50,
          "notes": "Seasonal rebalancing"          // optional
        }
        """
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)

        transfer = services.transfer_stock(
            product=ser.validated_data['product'],
            from_warehouse=ser.validated_data['from_warehouse'],
            to_warehouse=ser.validated_data['to_warehouse'],
            quantity=ser.validated_data['quantity'],
            notes=ser.validated_data.get('notes', ''),
            user=request.user,
        )

        out = InventoryTransferSerializer(transfer)
        return Response(out.data, status=status.HTTP_201_CREATED)
