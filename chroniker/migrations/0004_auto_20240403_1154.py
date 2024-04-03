# Generated by Django 3.2.23 on 2024-04-03 16:54

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('chroniker', '0003_auto_20200822_2026'),
    ]

    operations = [
        migrations.CreateModel(
            name='CallbackMethod',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200, verbose_name='name')),
                ('reference', models.CharField(max_length=200, verbose_name='reference')),
            ],
        ),
        migrations.AddField(
            model_name='job',
            name='callback_errors_to_subscribers',
            field=models.BooleanField(default=True, help_text='If checked, the stdout and stderr of a job will be sent to the callback if an error occur.'),
        ),
        migrations.AddField(
            model_name='job',
            name='callback_success_to_subscribers',
            field=models.BooleanField(default=False, help_text='If checked, the stdout of a job will be sent to the callback if not errors occur.'),
        ),
        migrations.AddField(
            model_name='job',
            name='callbacks',
            field=models.ManyToManyField(blank=True, related_name='callbacked_jobs', to='chroniker.CallbackMethod'),
        ),
    ]
