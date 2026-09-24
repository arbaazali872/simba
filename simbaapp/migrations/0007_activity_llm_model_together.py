from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('simbaapp', '0006_remove_old_activity_fields'),
    ]

    operations = [
        migrations.AlterField(
            model_name='activity',
            name='ai_model',
            field=models.CharField(choices=[('gpt', 'GPT'), ('mistral', 'Mistral'), ('together', 'Together AI')], default='gpt', max_length=20),
        ),
        migrations.AddField(
            model_name='activity',
            name='llm_model',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
    ]
