# Riadenie toku z a do 4DS

Streamlit aplikácia: `streamlit run 4ds.py`.

## Riadenie presunu späť zo 4DS

Do koreňa repozitára pridajte **4DS.xlsx**, prípadne ho nahrajte cez tretí
upload v aplikácii. Povinné stĺpce: `Sklad`, `Lokace`, `Produkt`, `Množstvo`,
`Skladom`. Jeden riadok predstavuje množstvo produktu na danej lokácii.
Opakované riadky rovnakého produktu a lokácie sa sčítajú; nevkladajte duplicitné
exporty. Všetky tri súbory majú predstavovať rovnaký dátum stavu zásob.

- **Primár:** množstvá z hlavného Excelu mimo SWAP a 4DS lokácií.
- **SWAP:** skutočné množstvá z prvého hárka SWAP Excelu (`Lokacia`, `Množstvo`),
  bez 4DS lokácií. Zahŕňa všetky GEOSIZE.
- **4DS:** množstvá z 4DS.xlsx na lokáciách začínajúcich `4D1-` a na
  `SHUTTLE-SWAP`. Tieto lokácie sa v ostatných zdrojoch nezapočítajú.

`Skladom` je súhrnný údaj, nesčítava sa. Výpočet používa množstvo na každej
lokácii a zásoby rovnakého SKU sčítava naprieč skladmi. SWAP snapshot je
autoritatívny pre SWAP zónu a 4DS.xlsx pre 4DS zónu; nejde o pripočítanie
ďalších množstiev k zásobe z hlavného exportu.

Denný predaj = predajnosť z hlavného Excelu / sledované obdobie predajnosti.
Pri nulovej alebo zápornej predajnosti sa použije náhradná mesačná predajnosť / 30.
Pri chýbajúcej predajnosti sa termín nevypočíta. Rozdielna predajnosť na riadkoch
rovnakého SKU je chyba vstupu, ktorú treba opraviť.

Pokrytie primáru = primár / denný predaj. SWAP samostatne = SWAP / denný predaj.
Spoločné pokrytie = (primár + SWAP) / denný predaj, pri predpoklade spotreby
primáru pred SWAP-om. Prvý návrat nastane najneskôr o
`max(0, spoločné pokrytie - hranica spätného závozu)` dní. Dátumy sa počítajú
od zvoleného dátumu zásob a dni sa zaokrúhľujú nadol.

Plán vyberá celú fyzickú lokáciu (Sklad + Lokace), vrátane všetkých jej
produktov. Skorší termín jedného produktu môže spustiť návrat celej zmiešanej
lokácie. Po každom návrate sa zohľadní privezené množstvo všetkých produktov
a vypočíta sa termín ďalšej lokácie. Neodhaduje sa počet kusov cez m³.
Lokácia s produktom bez predajnosti sa označí a automatický plán ju vynechá.

V záložke je samostatný parameter **Cieľová zásoba na primári po spätnom
presune (dni)**, predvolene **30 dní**. Hranica spätného závozu naďalej určuje,
kedy začať návrat podľa spoločného pokrytia primáru a SWAP-u. Veľkosť závozu
určuje nový cieľ: počet celých lokácií potrebných na pokrytie samotného primáru
aspoň na zvolený počet dní. SWAP sa do cieľa nezapočítava. Pred závozom sa
odpočíta očakávaný predaj do jeho termínu (primár sa spotrebúva pred SWAP-om).
Celé lokácie sa vyberajú podľa názvu lokácie, vrátane všetkých ich produktov.
Ostatné produkty zmiešanej lokácie sa vezú spolu; cieľ sa vyhodnocuje pre
produkt, ktorý spustil daný závoz. Plán ukazuje pokrytie po celom závoze a
upozorní, ak dostupné lokácie nestačia na dosiahnutie cieľa. Cieľ musí byť
väčší ako hranica spustenia návratu, aby ďalší závoz nevznikal okamžite znovu.

Záložka ukazuje potrebu návratov v zvolenom horizonte. Neobmedzuje plán dennou
dopravnou kapacitou, neplánuje nové nákupy ani čas prepravy. Nie je naviazaná
na hypotetické presuny zo simulácie ani na rozmerové/GEOSIZE filtre vývozu.
Export obsahuje pokrytie, plán a detail všetkých troch zón.

Testy výpočtu: `python -m unittest -v test_return_flow.py`.
