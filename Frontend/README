Projekt: Prawdowid
Inicjatywa: HackNation 2026
Zleceniodawca / Instytucja: Ministerstwo Spraw Zagranicznych
Dokument: System Identyfikacji Wizualnej (SIW)
Wersja: 1.0 (Draft Hackathonowy)

System Identyfikacji Wizualnej (SIW) — Wyszukiwarka „Prawdowid”

Kontekst projektu:

Dokument opisuje standardy wizualne dla systemu wyszukiwawczego Prawdowid, realizowanego w ramach maratonu technologicznego HackNation 2026 pod patronatem Ministerstwa Spraw Zagranicznych.

1. Filozofia wizualna i charakter marki

System wizualny projektu Prawdowid bazuje na organicznym minimalizmie i spokoju wizualnym. Zamiast sterylnej bieli oraz agresywnych, cyfrowych odcieni niebieskiego, interfejs wyszukiwarki wykorzystuje naturalne barwy ziemi inspirowane upierzeniem wróbla zwyczajnego (Passer domesticus).

Koncepcja ta buduje wrażenie wiarygodności, spokoju i autentyczności — wartości kluczowych w systemach weryfikacji informacji i wyszukiwania realizowanych dla sektora publicznego.

Kluczowe filary estetyczne:

Ergonomia wzrokowa: Przygaszone tło w odcieniu papierowej bieli redukuje zmęczenie wzroku i sprzyja długiej pracy z tekstem.

Przyjazna geometria: Wszechobecność pełnych owali (pill-shapes) przełamuje urzędowy chłód i nadaje systemowi przystępny, nowoczesny charakter.

Dyscyplina przestrzeni (Whitespace): Maksymalne uproszczenie widoku głównego kieruje uwagę użytkownika wyłącznie na jedno kluczowe zadanie: wpisanie zapytania.

2. Logo i sygnet

Znak identyfikacyjny projektu łączy przejrzystą typografię z dyskretnym symbolem ptaka (wróbla), nawiązującym do zwinności, powszechności i czujności.

       .---.
      /  o  \  
     (  ---  )  prawdowid
      `-----'


Logotyp: Zapis nazwy prawdowid wyłącznie małymi literami. Używamy geometrycznego, humanistycznego kroju bezszeryfowego. Kolor podstawowy logotypu to głęboki antracyt (#1F1E1E).

Sygnet: Stylizowana, płaska sylwetka wróbla w odcieniach brązu, osadzona w przestrzeni logotypu lub występująca samodzielnie jako favicon / ikona aplikacji.

Pole ochronne: Minimalny odstęp wokół znaku odpowiada wysokości litery „o” w logotypie.

3. Paleta kolorystyczna

Paleta barw czerpie z upierzenia wróbla. Została zaprojektowana z myślą o zachowaniu wysokiej dostępności (WCAG 2.1 AA) przy jednoczesnym zachowaniu ciepłego, organicznego tonu.

Nazwa barwy

Hex

RGB

Rola w interfejsie

Złamana biel

#E6E2DC

230, 226, 220

Główne tło strony oraz kolor tekstu na ciemnych przyciskach.

Kremowy (Input)

#F4F1EA

244, 241, 234

Wnętrze pola paska wyszukiwania (delikatne odcięcie od tła).

Czarny (Antracyt)

#1F1E1E

31, 30, 30

Główna typografia, logotyp, elementy nawigacyjne.

Kasztanowy

#6E3B2B

110, 59, 43

Akcent główny: przycisk „Szukaj”, aktywne elementy interaktywne.

Ciepły brąz

#7A5C43

122, 92, 67

Ikona lupy, stany najechania (hover), odwiedzone odnośniki.

Beżowy

#C5B299

197, 178, 153

Tło smukłej stopki informacyjnej u dołu ekranu.

Popielaty

#8C8E90

140, 142, 144

Cienkie obramowanie paska wyszukiwania oraz linie podziału.

Jasnobrązowy

#9E826B

158, 130, 107

Metadane, podpowiedzi, kody źródłowe / daty w wynikach.

4. Geometria i stylistyka interfejsu (UI)

Forma owalna (Pill-shape)

Wszystkie aktywne elementy nawigacyjne i formularze posiadają skrajnie zaokrąglone krawędzie (border-radius: 9999px).

Złota zasada przycisku akcji

Główny przycisk „Szukaj” jest osadzony wewnątrz pola wyszukiwania. Posiada on krawędź/odstęp w kolorze tła pola tekstowego (#F4F1EA), tworząc efekt „pływającego owalu” idealnie wpisanego w większą ramę.

Czysta strefa robocza

Obszar pod paskiem wyszukiwania pozbawiony jest zbędnych przycisków, tagów czy kategorii. Zachowano maksymalną czystość kompozycyjną.

5. Komponenty widoku głównego

Górny pasek (Header):

Prawa strona: Dyskretne linki tekstowe (Poczta, Ustawienia, MSZ Portal) w kolorze #1F1E1E.

Sekcja centralna (Hero):

Wyśrodkowane logo prawdowid z sygnetem.

Owalny pasek wyszukiwania o wymiarach max. 680px.

Ikona lupy po lewej stronie w kolorze #7A5C43.

Owalny przycisk w kolorze #6E3B2B z białą typografią po prawej stronie.

Dolny pasek (Footer):

Pełnoszerokościowy pas w odcieniu #C5B299.

Informacje stopki: HackNation 2026, Ministerstwo Spraw Zagranicznych, O usłudze, Prywatność.

6. Typografia

Zalecane kroje bezszeryfowe (Open Source):

Główny krój: Inter lub Plus Jakarta Sans.

Krój pomocniczy / Kod: JetBrains Mono (w sekcjach technicznych weryfikacji danych).

Skala typograficzna:

Logotyp: 48px / Bold (Semi-Bold)

Tekst w polu wyszukiwania: 18px / Regular

Przycisk „Szukaj”: 16px / Medium

Linki nawigacyjne: 14px / Regular

Stopka i metadane: 13px / Regular

7. Specyfikacja techniczna (CSS Tokens)

/* Prawdowid UI Design Tokens - HackNation 2026 / MSZ */

:root {
  /* Brand Palette */
  --prawdowid-bg-canvas: #E6E2DC;
  --prawdowid-bg-input: #F4F1EA;
  --prawdowid-bg-footer: #C5B299;
  
  --prawdowid-text-primary: #1F1E1E;
  --prawdowid-text-secondary: #9E826B;
  --prawdowid-text-on-accent: #E6E2DC;
  
  --prawdowid-accent-main: #6E3B2B;
  --prawdowid-accent-hover: #7A5C43;
  --prawdowid-border-light: #8C8E90;

  /* Shape & Spacing Tokens */
  --prawdowid-radius-pill: 9999px;
  --prawdowid-input-border-width: 1.5px;
  --prawdowid-btn-inset-border: 4px solid var(--prawdowid-bg-input);
}

/* Example Search Container Component Styling */
.prawdowid-search-bar {
  display: flex;
  align-items: center;
  background-color: var(--prawdowid-bg-input);
  border: var(--prawdowid-input-border-width) solid var(--prawdowid-border-light);
  border-radius: var(--prawdowid-radius-pill);
  padding: 4px 6px 4px 20px;
  width: 100%;
  max-width: 680px;
}

.prawdowid-search-button {
  background-color: var(--prawdowid-accent-main);
  color: var(--prawdowid-text-on-accent);
  border-radius: var(--prawdowid-radius-pill);
  padding: 10px 24px;
  border: none;
  outline: var(--prawdowid-btn-inset-border);
  cursor: pointer;
  transition: background-color 0.2s ease;
}

.prawdowid-search-button:hover {
  background-color: var(--prawdowid-accent-hover);
}
