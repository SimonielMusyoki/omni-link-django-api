from rest_framework.pagination import PageNumberPagination


class StandardResultsSetPagination(PageNumberPagination):
    """Default API pagination with configurable page_size per request."""

    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100

