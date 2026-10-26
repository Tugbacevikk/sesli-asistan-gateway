# models/

Model dosyaları repoya girmez. İndirmek ve doğrulamak için:

```bash
python tools/models/indir.py              # deploy/models.yaml'daki varsayılanlar
python tools/models/indir.py --dogrula    # yalnızca SHA256 kontrolü
```

İnternetsiz sunucu: internetli makinede indirip `models/` klasörünü (MANIFEST.json dahil) taşıyın,
sonra `--dogrula` ile kontrol edin.
