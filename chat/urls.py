from django.urls import path
from . import views

urlpatterns = [
    path('', views.chat_view, name='chat'),
    path('api/message/', views.chat_message_api, name='chat_message_api'),
    path('api/search/', views.file_search_api, name='file_search_api'),
    path('api/fetch_file/', views.fetch_file_api, name='fetch_file_api'),
    path('api/list_dir/', views.list_dir_api, name='list_dir_api'),
    path('api/read_file/', views.read_file_content_api, name='read_file_content_api'),
]
