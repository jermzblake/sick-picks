from django.urls import path

from picks import views

urlpatterns = [
    path('games/', views.game_slate, name='game-slate'),
    path('games/<int:game_id>/pick/', views.submit_game_pick, name='submit-pick'),
    path('history/', views.pick_history, name='pick-history'),
]
