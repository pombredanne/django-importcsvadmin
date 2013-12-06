import csv, sys
from functools import update_wrapper
from itertools import count

from django.conf.urls import url
from django.contrib import admin
from django.contrib import messages
from django.core.urlresolvers import reverse
from django.db import transaction
from django.http import HttpResponse
from django import forms
from django.utils import six
from django.utils.translation import ugettext_lazy as _
from django.views.generic.edit import FormView


class ImportCSVForm(forms.Form):
    csv_file = forms.FileField(required=True, label=_('CSV File'))


class ImportCSVAdminView(FormView):
    form_class = ImportCSVForm
    model_admin = None

    def _get_meta(self):
        opts = self.model_admin.model._meta
        app_label = opts.app_label
        object_name = opts.object_name.lower()
        return (app_label, object_name)

    def get_template_names(self):
        importcsv_template = self.model_admin.importcsv_template
        if importcsv_template is not None:
            return importcsv_template
        else:
            app_label, object_name = self._get_meta()
            return [
                'admin/%s/%s/csv_import.html' % (app_label, object_name),
                'admin/%s/csv_import.html' % app_label,
                'admin/csv_import.html',
            ]

    def get_success_url(self):
        app_label, object_name = self._get_meta()
        return reverse('admin:%s_%s_changelist' % (app_label, object_name))

    def form_valid(self, form):
        try:
            self.import_csv(form.cleaned_data['csv_file'])
        except ValueError:
            return self.form_invalid(form)
        return super(ImportCSVAdminView, self).form_valid(form)

    def get_context_data(self, **kwargs):
        context = super(ImportCSVAdminView, self).get_context_data(**kwargs)
        context['opts'] = self.model_admin.model._meta
        return context

    @transaction.commit_on_success
    def import_csv(self, file_):
        reader = csv.DictReader(
            file_,
            fieldnames=self.model_admin.importer_class._meta.fields,
            dialect=self.model_admin.dialect,
        )

        reader_iter = iter(six.moves.zip(count(start=1), reader))
        if self.model_admin.skip_firstline:
            six.advance_iterator(reader_iter)

        for i, row in reader_iter:
            try:
                self.process_row(row)
            except ValueError as e:
                messages.error(self.request, _("Couldn't process row #%d: %s") % (i, e.message))
                messages.error(self.request, _("Import has been canceled. Nothing was imported."))
                six.reraise(*sys.exc_info())

    def process_row(self, row):
        importer = self.model_admin.importer_class(data=row)
        if not importer.is_valid():
            # XXX: Just get the first error message
            fieldname, errors = importer.errors.items()[0]
            field = importer[fieldname]
            raise ValueError('%s - %s' % (field.label, errors[0]))
        return importer.save()


class ImportCSVModelAdmin(admin.ModelAdmin):
    importcsv_view_class = ImportCSVAdminView
    importcsv_template = None

    dialect = csv.excel
    skip_firstline = True

    @property
    def change_list_template(self):
        opts = self.model._meta
        return [
            'admin/%s/%s/change_list_csv.html' % (opts.app_label, opts.object_name.lower()),
            'admin/%s/change_list_csv.html' % opts.app_label,
            'admin/change_list_csv.html',
        ]

    def get_urls(self):
        # XXX: Shamelessly copied from django/contrib/admin/options.py
        def wrap(view):
            def wrapper(*args, **kwargs):
                return self.admin_site.admin_view(view)(*args, **kwargs)
            return update_wrapper(wrapper, view)

        info = self.model._meta.app_label, self.model._meta.module_name

        urlpatterns = super(ImportCSVModelAdmin, self).get_urls()

        extra_urls = [
            url(r'^import-csv/$',
                wrap(self.importcsv_view),
                name='%s_%s_importcsv' % info),
            url(r'^import-csv/template.csv$',
                wrap(self.download_csv_template),
                name='%s_%s_csvtemplate' % info),
        ]
        return extra_urls + urlpatterns

    @property
    def importcsv_view(self):
        return self.importcsv_view_class.as_view(model_admin=self)

    def download_csv_template(self, request):
        def get_label(form, fname):
            field = form[fname]
            label = field.label
            if field.field.required:
                label = '%s*' % label
            return label

        importer = self.importer_class()

        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachement; filename="template.csv"'

        writer = csv.writer(response, dialect=self.dialect)

        fields = importer._meta.fields
        labels = [get_label(importer, fname) for fname in fields]
        writer.writerow(labels)

        return response
