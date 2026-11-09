from django.urls import path

from picks import views

urlpatterns = [
    path('', views.home, name='home'),
    path('how-to-play/', views.how_to_play, name='how-to-play'),
    path('games/', views.game_slate, name='game-slate'),
    path('games/<int:game_id>/pick/', views.submit_game_pick, name='submit-pick'),
    path('history/', views.pick_history, name='pick-history'),
    path(
        'history/<str:username>/',
        views.pick_history,
        name='user-pick-history',
    ),
    path('leaderboard/', views.leaderboard, name='leaderboard'),
    path('api/leaderboard/', views.leaderboard_api, name='leaderboard-api'),
]
