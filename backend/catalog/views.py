from django.db import transaction
from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404
from rest_framework import generics, permissions, status
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from catalog.models import (
    AuditLog,
    Availability,
    Merchant,
    PriceRule,
    Product,
    ProductVariant,
)
from catalog.serializers import (
    InventoryAdjustmentRequestSerializer,
    InventoryAdjustmentSerializer,
    ProductActivitySerializer,
    ProductListSerializer,
    ProductUpdateSerializer,
)
from catalog.services import adjust_inventory


class ProductPagination(PageNumberPagination):
    page_size = 25


class ProductListView(generics.ListAPIView):
    serializer_class = ProductListSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = ProductPagination

    def get_queryset(self):
        merchant_id = self.request.query_params.get("merchant_id")

        if not merchant_id:
            raise ValidationError({"merchant_id": "This parameter is required."})

        try:
            merchant_id = int(merchant_id)
        except (TypeError, ValueError):
            raise ValidationError({"merchant_id": "Must be a valid integer."})

        if merchant_id <= 0:
            raise ValidationError({"merchant_id": "Must be a positive integer."})

        get_object_or_404(
            Merchant.objects.filter(members=self.request.user),
            pk=merchant_id,
        )

        # The serializer renders category.name for every row.
        queryset = (
            Product.objects.filter(merchant_id=merchant_id)
            .select_related("category")
            .order_by("id")
        )

        search = self.request.query_params.get("search", "").strip()

        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) | Q(sku__icontains=search)
            )

        return queryset


class ProductEditorView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        merchant_id = request.query_params.get("merchant_id")

        if not merchant_id:
            raise ValidationError({"merchant_id": "This parameter is required."})

        try:
            merchant_id = int(merchant_id)
        except (TypeError, ValueError):
            raise ValidationError({"merchant_id": "Must be a valid integer."})

        if merchant_id <= 0:
            raise ValidationError({"merchant_id": "Must be a positive integer."})

        get_object_or_404(
            Merchant.objects.filter(members=request.user),
            pk=merchant_id,
        )

        product = get_object_or_404(
            Product.objects.select_related("category"),
            pk=pk,
            merchant_id=merchant_id,
        )

        # Load inventory (joined) and prices (one extra query) for all
        # variants up front, instead of two queries per variant.
        variants = list(
            ProductVariant.objects.filter(product_id=product.pk)
            .select_related("inventory")
            .prefetch_related(
                Prefetch("price_rules", queryset=PriceRule.objects.order_by("id"))
            )
            .order_by("id")
        )

        prices_by_variant = {}
        inventory_by_variant = {}

        for variant in variants:
            prices_by_variant[variant.pk] = [
                {
                    "id": price.pk,
                    "tier": price.tier,
                    "amount": f"{price.amount:.2f}",
                }
                for price in variant.price_rules.all()
            ]

            # A variant may have no inventory row; the reverse one-to-one
            # accessor raises in that case, so fall back to None.
            inventory = getattr(variant, "inventory", None)

            inventory_by_variant[variant.pk] = (
                {
                    "id": inventory.pk,
                    "quantity": inventory.quantity,
                    "reserved": inventory.reserved,
                }
                if inventory
                else None
            )

        availability = list(
            Availability.objects.filter(product_id=product.pk)
            .order_by("id")
            .values(
                "id",
                "channel",
                "enabled",
            )
        )

        return Response(
            {
                "id": product.pk,
                "name": product.name,
                "sku": product.sku,
                "description": product.description,
                "status": product.status,
                "category": {
                    "id": product.category_id,
                    "name": product.category.name,
                },
                "variants": [
                    {
                        "id": variant.pk,
                        "name": variant.name,
                        "sku": variant.sku,
                        "prices": prices_by_variant[variant.pk],
                        "inventory": inventory_by_variant.get(variant.pk),
                    }
                    for variant in variants
                ],
                "availability": availability,
            }
        )


class ProductDetailView(generics.RetrieveUpdateAPIView):
    serializer_class = ProductUpdateSerializer
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = [
        "get",
        "patch",
        "head",
        "options",
    ]

    def get_queryset(self):
        merchant_id = self.request.query_params.get("merchant_id")

        if not merchant_id:
            raise ValidationError({"merchant_id": "This parameter is required."})

        try:
            merchant_id = int(merchant_id)
        except (TypeError, ValueError):
            raise ValidationError({"merchant_id": "Must be a valid integer."})

        if merchant_id <= 0:
            raise ValidationError({"merchant_id": "Must be a positive integer."})

        get_object_or_404(
            Merchant.objects.filter(members=self.request.user),
            pk=merchant_id,
        )

        return Product.objects.filter(merchant_id=merchant_id).select_related(
            "category"
        )

    def perform_update(self, serializer):
        tracked_fields = (
            "name",
            "sku",
            "description",
            "status",
            "category_id",
            "image_url",
        )

        with transaction.atomic():
            product = serializer.instance

            # Lock the row before comparing old and new values.
            locked_product = Product.objects.select_for_update().get(pk=product.pk)

            before = {field: getattr(locked_product, field) for field in tracked_fields}

            # Use the locked instance for the actual update.
            serializer.instance = locked_product
            updated_product = serializer.save()

            changes = {}

            for field in tracked_fields:
                old_value = before[field]
                new_value = getattr(updated_product, field)

                if old_value != new_value:
                    changes[field] = {
                        "from": old_value,
                        "to": new_value,
                    }

            if changes:
                AuditLog.objects.create(
                    product=updated_product,
                    actor=self.request.user,
                    action="product.updated",
                    details={"changes": changes},
                )


class ProductActivityView(generics.ListAPIView):
    serializer_class = ProductActivitySerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = ProductPagination

    def get_queryset(self):
        merchant_id = self.request.query_params.get("merchant_id")

        if not merchant_id:
            raise ValidationError({"merchant_id": "This parameter is required."})

        try:
            merchant_id = int(merchant_id)
        except (TypeError, ValueError):
            raise ValidationError({"merchant_id": "Must be a valid integer."})

        if merchant_id <= 0:
            raise ValidationError({"merchant_id": "Must be a positive integer."})

        get_object_or_404(
            Merchant.objects.filter(members=self.request.user),
            pk=merchant_id,
        )

        product = get_object_or_404(
            Product.objects.filter(merchant_id=merchant_id),
            pk=self.kwargs["pk"],
        )

        return (
            AuditLog.objects.filter(product_id=product.pk)
            .select_related("actor")
            .order_by("-created_at", "-id")
        )


def get_member_merchant_id(request):
    """Validate ?merchant_id= and require the user to be a member.

    Same checks, in the same order, as the existing views: 400 for a
    missing or malformed id, 404 when the user is not a member.
    """
    merchant_id = request.query_params.get("merchant_id")

    if not merchant_id:
        raise ValidationError({"merchant_id": "This parameter is required."})

    try:
        merchant_id = int(merchant_id)
    except (TypeError, ValueError):
        raise ValidationError({"merchant_id": "Must be a valid integer."})

    if merchant_id <= 0:
        raise ValidationError({"merchant_id": "Must be a positive integer."})

    get_object_or_404(
        Merchant.objects.filter(members=request.user),
        pk=merchant_id,
    )

    return merchant_id


class InventoryAdjustmentView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, product_pk, variant_pk):
        merchant_id = get_member_merchant_id(request)

        # The variant must belong to this product, and the product to this
        # merchant; anything else is indistinguishable from "not found".
        variant = get_object_or_404(
            ProductVariant,
            pk=variant_pk,
            product_id=product_pk,
            product__merchant_id=merchant_id,
        )

        serializer = InventoryAdjustmentRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        adjustment = adjust_inventory(
            variant=variant,
            actor=request.user,
            delta=serializer.validated_data["delta"],
            reason=serializer.validated_data["reason"],
        )

        return Response(
            InventoryAdjustmentSerializer(adjustment).data,
            status=status.HTTP_201_CREATED,
        )
