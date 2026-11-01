from django.contrib.auth import login
from django.contrib.auth.views import RedirectURLMixin
from django.db import IntegrityError, transaction
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.generic import CreateView

from accounts.forms import SignUpForm


@method_decorator(never_cache, name='dispatch')
class SignUpView(RedirectURLMixin, CreateView):
    form_class = SignUpForm
    template_name = 'registration/signup.html'
    next_page = reverse_lazy('game-slate')

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect(self.get_success_url())
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['next'] = self.request.POST.get(
            self.redirect_field_name,
            self.request.GET.get(self.redirect_field_name, ''),
        )
        return context

    def form_valid(self, form):
        try:
            with transaction.atomic():
                self.object = form.save()
        except IntegrityError:
            form.add_error('username', 'A user with that username already exists.')
            return self.form_invalid(form)
        login(self.request, self.object)
        return redirect(self.get_success_url())
