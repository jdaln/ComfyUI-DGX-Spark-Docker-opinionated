"""Split Sentences and Numbers to Words (Finnish): text prepared for speech.

Split Sentences cuts a text into sentences, one list item each, so a text to
speech node speaks them one by one and stays within what it can say in one go.
A full stop after an abbreviation such as esim. or klo, inside a time such as
9.30 or after an ordinal such as 3. does not end a sentence. In dictation mode
each sentence is cut further at its commas.

Numbers to Words (Finnish) writes out what Chatterbox's Finnish model cannot
read: digits, times, prices, dates, decimals, percentages, phone numbers, units
and common abbreviations. Read back with Whisper, its output for klo 9.30 was
"klo urjinus kolpias" and a date started an endless loop, while the same
sentences written in words came back exactly. A number takes the case of the
word after it, so 3. kerroksessa becomes kolmannessa kerroksessa and 2 euron
kahden euron; a number before a word in the partitive, as in 3 omenaa, stays in
the nominative, as Finnish has it. Use it on the text that is spoken only, and
keep the original for subtitles.
"""

import re

_BACK = set("aouAOU")
ONES = ["nolla", "yksi", "kaksi", "kolme", "neljä", "viisi", "kuusi", "seitsemän", "kahdeksan", "yhdeksän"]
# nominative: (stem for most cases, essive stem, illative, partitive)
_CARDINAL = {
    "nolla": ("nolla", "nolla", "nollaan", "nollaa"),
    "yksi": ("yhde", "yhte", "yhteen", "yhtä"), "kaksi": ("kahde", "kahte", "kahteen", "kahta"),
    "kolme": ("kolme", "kolme", "kolmeen", "kolmea"), "neljä": ("neljä", "neljä", "neljään", "neljää"),
    "viisi": ("viide", "viite", "viiteen", "viittä"), "kuusi": ("kuude", "kuute", "kuuteen", "kuutta"),
    "seitsemän": ("seitsemä", "seitsemä", "seitsemään", "seitsemää"),
    "kahdeksan": ("kahdeksa", "kahdeksa", "kahdeksaan", "kahdeksaa"),
    "yhdeksän": ("yhdeksä", "yhdeksä", "yhdeksään", "yhdeksää"),
    "kymmenen": ("kymmene", "kymmene", "kymmeneen", "kymmentä"), "sata": ("sada", "sata", "sataan", "sataa"),
    "tuhat": ("tuhanne", "tuhante", "tuhanteen", "tuhatta"),
    "miljoona": ("miljoona", "miljoona", "miljoonaan", "miljoonaa"),
}
# ordinal roots: the nominative is root + s, and the other forms follow from the root
_ORDINAL_ROOT = {"kolme": "kolma", "neljä": "neljä", "viisi": "viide", "kuusi": "kuude", "seitsemän": "seitsemä",
                 "kahdeksan": "kahdeksa", "yhdeksän": "yhdeksä", "kymmenen": "kymmene", "sata": "sada",
                 "tuhat": "tuhanne", "miljoona": "miljoona", "yksi": "yhde", "kaksi": "kahde"}
_ENDINGS = {"gen": "n", "iness": "ssa", "elat": "sta", "adess": "lla", "ablat": "lta", "allat": "lle",
            "transl": "ksi", "ess": "na"}
_SCALES = ((10**6, "miljoona"), (1000, "tuhat"), (100, "sata"))
MONTHS = ["tammikuuta", "helmikuuta", "maaliskuuta", "huhtikuuta", "toukokuuta", "kesäkuuta", "heinäkuuta",
          "elokuuta", "syyskuuta", "lokakuuta", "marraskuuta", "joulukuuta"]
# unit: (one, several)
UNITS = {"€": ("euro", "euroa"), "e": ("euro", "euroa"), "snt": ("sentti", "senttiä"), "%": ("prosentti", "prosenttia"),
         "km": ("kilometri", "kilometriä"), "m": ("metri", "metriä"), "cm": ("senttimetri", "senttimetriä"),
         "mm": ("millimetri", "millimetriä"), "kg": ("kilo", "kiloa"), "g": ("gramma", "grammaa"),
         "l": ("litra", "litraa"), "dl": ("desilitra", "desilitraa"), "h": ("tunti", "tuntia"),
         "min": ("minuutti", "minuuttia"), "s": ("sekunti", "sekuntia"), "°C": ("aste", "astetta"),
         "°": ("aste", "astetta"), "kpl": ("kappale", "kappaletta")}
ABBREVIATIONS = {"klo": "kello", "esim.": "esimerkiksi", "mm.": "muun muassa", "jne.": "ja niin edelleen",
                 "ns.": "niin sanottu", "n.": "noin", "ym.": "ynnä muuta", "yms.": "ynnä muuta sellaista",
                 "tms.": "tai muuta sellaista", "ks.": "katso", "vrt.": "vertaa", "ts.": "toisin sanoen",
                 "puh.": "puhelin", "os.": "osoite"}
_WEEKDAY_FROM = {"ma": "maanantaista", "ti": "tiistaista", "ke": "keskiviikosta", "to": "torstaista",
                 "pe": "perjantaista", "la": "lauantaista", "su": "sunnuntaista"}
_WEEKDAY_TO = {"ma": "maanantaihin", "ti": "tiistaihin", "ke": "keskiviikkoon", "to": "torstaihin",
               "pe": "perjantaihin", "la": "lauantaihin", "su": "sunnuntaihin"}


def _harmony(word, ending):
    """Back-vowel words take a, front-vowel words ä."""
    if any(ch in _BACK for ch in word):
        return ending
    return ending.replace("a", "ä")


def _inflect(stem, essive, illative, partitive, case):
    if case == "part":
        return partitive
    if case == "illat":
        return illative
    if case == "ess":
        return essive + _harmony(essive, "na")
    return stem + _harmony(stem, _ENDINGS[case])


def _word(word, case):
    """A basic numeral word (yksi ... kymmenen, sata, tuhat, miljoona) in a case."""
    return word if case == "nom" else _inflect(*_CARDINAL[word], case)


def _small(n, case):
    """0 to 99."""
    if n < 10:
        return _word(ONES[n], case)
    if n == 10:
        return _word("kymmenen", case)
    if n < 20:
        return _word(ONES[n - 10], case) + "toista"
    tens, unit = divmod(n, 10)
    ten = _CARDINAL["kymmenen"][3] if case == "nom" else _word("kymmenen", case)
    return _word(ONES[tens], case) + ten + (_word(ONES[unit], case) if unit else "")


def cardinal(n, case="nom"):
    """Finnish cardinal in a case: nom, gen, part, iness, elat, illat, adess, ablat, allat, ess, transl.

    Groups of millions, thousands and hundreds are written apart, which Finnish
    spelling does not do, so that long numbers read well when spoken."""
    if n < 0:
        return "miinus " + cardinal(-n, case)
    groups = []
    for size, word in _SCALES:
        if n >= size:
            count, n = divmod(n, size)
            if count == 1:
                groups.append(_word(word, case))
            else:
                scale = _CARDINAL[word][3] if case == "nom" else _word(word, case)
                groups.append(cardinal(count, case) + scale)
    if n or not groups:
        groups.append(_small(n, case))
    return " ".join(groups)


def _ordinal_word(word, compound, case):
    """One ordinal word; inside a longer ordinal 1 and 2 are yhdes and kahdes."""
    if word in ("yksi", "kaksi") and not compound:
        nominative, stem, partitive = (("ensimmäinen", "ensimmäise", "ensimmäistä") if word == "yksi"
                                       else ("toinen", "toise", "toista"))
        if case == "nom":
            return nominative
        return _inflect(stem, stem, stem + "en", partitive, case)
    root = _ORDINAL_ROOT[word]
    if case == "nom":
        return root + "s"
    return _inflect(root + "nne", root + "nte", root + "nteen", root + _harmony(root, "tta"), case)


def _small_ordinal(n, case, compound):
    if n < 10:
        return _ordinal_word(ONES[n], compound, case)
    if n == 10:
        return _ordinal_word("kymmenen", compound, case)
    if n < 20:
        return _ordinal_word(ONES[n - 10], True, case) + "toista"
    tens, unit = divmod(n, 10)
    return (_ordinal_word(ONES[tens], True, case) + _ordinal_word("kymmenen", True, case)
            + (_ordinal_word(ONES[unit], True, case) if unit else ""))


def ordinal(n, case="nom"):
    """Finnish ordinal (3 -> kolmas) in a case."""
    groups = []
    compound = n > 10
    for size, word in _SCALES:
        if n >= size:
            count, n = divmod(n, size)
            head = "" if count == 1 else ordinal(count, case).replace(" ", "")
            if count == 2:
                head = _ordinal_word("kaksi", True, case)
            groups.append(head + _ordinal_word(word, True, case))
    if n:
        groups.append(_small_ordinal(n, case, compound))
    return "".join(groups)


_ILLATIVE = re.compile(r"(?:seen|siin|hin|hun|hyn|hon|hön|hen|hän|aan|ään|een|iin|oon|öön|uun|yyn)$")
# A word ending in -n is as often a verb (asuin, menen) as a genitive, -sta can be
# a partitive (ihmistä) as well as an elative, and many nominatives end in -na or
# -a. After a cardinal these cases are only read off words that often follow a
# number. An ordinal agrees with its noun in every case, so after an ordinal the
# genitive and elative are read off the ending.
_GENITIVE = {"euron", "sentin", "vuoden", "kuukauden", "viikon", "päivän", "tunnin", "minuutin", "sekunnin",
             "kilometrin", "metrin", "kilon", "gramman", "litran", "desilitran", "hengen", "henkilön", "lapsen",
             "kerroksen", "luokan", "sivun", "pisteen", "prosentin", "asteen", "kappaleen", "kerran", "aseman",
             "linjan", "laiturin", "raiteen", "huoneen", "talon", "oven", "rivin", "kohdan", "tehtävän", "kysymyksen"}
_ELATIVE = {"eurosta", "sentistä", "vuodesta", "kuukaudesta", "viikosta", "päivästä", "tunnista", "minuutista",
            "sekunnista", "prosentista", "asteesta", "kerroksesta", "luokasta", "sivulta", "kohdasta", "tehtävästä"}
_ESSIVE = {"päivänä", "vuonna", "kertana", "viikkona", "kuukautena", "iltana", "aamuna", "yönä", "kesänä",
           "talvena", "keväänä", "syksynä", "maanantaina", "tiistaina", "keskiviikkona", "torstaina", "perjantaina",
           "lauantaina", "sunnuntaina", "vuosisatana", "kautena", "jaksona", "tuntina", "päivinä", "vuosina"}
_PARTITIVE = {"kertaa", "vuotta", "päivää", "kuukautta", "viikkoa", "kertaa"}
_SUFFIX_CASES = (("ssa", "iness"), ("ssä", "iness"), ("sta", "elat"), ("stä", "elat"), ("lla", "adess"),
                 ("llä", "adess"), ("lta", "ablat"), ("ltä", "ablat"), ("lle", "allat"), ("ksi", "transl"))


def case_of(word, ordinal=False):
    """Grammatical case read from the word after a number, enough to inflect the number."""
    w = word.lower().strip(".,;:!?\"'()")
    if w in _ESSIVE:
        return "ess"
    if w in _GENITIVE:
        return "gen"
    if w in _ELATIVE:
        return "elat"
    if ordinal and w in _PARTITIVE:
        return "part"
    for ending, case in _SUFFIX_CASES:
        if case == "elat" and not ordinal:
            continue
        if w.endswith(ending) and len(w) > len(ending) + 1:
            return case
    if _ILLATIVE.search(w) and len(w) > 4:
        return "illat"
    if ordinal and w.endswith("n") and len(w) > 3:
        return "gen"
    return "nom"


def suffix_case(suffix):
    """Case of an ending written after a colon, as in 5:ssä or 3:n."""
    suffix = suffix.lower()
    for ending, case in _SUFFIX_CASES:
        if suffix == ending:
            return case
    if suffix in ("n",):
        return "gen"
    if suffix in ("na", "nä"):
        return "ess"
    if suffix in ("a", "ä", "ta", "tä"):
        return "part"
    if _ILLATIVE.search("xx" + suffix) or suffix in ("een", "iin", "seen", "aan", "ään"):
        return "illat"
    return None


def _number(text):
    """'1 000' or '1000' to int."""
    return int(text.replace(" ", "").replace(" ", ""))


def _amount(whole, fraction, unit):
    """2,50 € -> kaksi euroa viisikymmentä senttiä; 1,5 l -> puolitoista litraa."""
    one, several = UNITS[unit]
    if unit in ("€", "e"):
        euros, cents = _number(whole), int((fraction or "0").ljust(2, "0")[:2])
        spoken = []
        if euros or not cents:
            spoken.append(f"{cardinal(euros)} {one if euros == 1 else several}")
        if cents:
            spoken.append(f"{cardinal(cents)} {'sentti' if cents == 1 else 'senttiä'}")
        return " ".join(spoken)
    return f"{decimal(whole, fraction)} {one if _number(whole) == 1 and not fraction else several}"


def decimal(whole, fraction):
    if not fraction:
        return cardinal(_number(whole))
    if fraction == "5":
        return "puolitoista" if _number(whole) == 1 else f"{cardinal(_number(whole))} ja puoli"
    return f"{cardinal(_number(whole))} pilkku {' '.join(ONES[int(d)] for d in fraction)}"


def _time(hours, minutes):
    h = cardinal(int(hours))
    if minutes is None or int(minutes) == 0:
        return h
    if minutes.startswith("0"):
        return f"{h} nolla {ONES[int(minutes[1])]}"
    return f"{h} {cardinal(int(minutes))}"


def _time_case(hours, minutes, case):
    h = cardinal(int(hours), case)
    if minutes is None or int(minutes) == 0:
        return h
    return f"{h} {cardinal(int(minutes), case)}"


_NUM = r"\d{1,3}(?:[  ]\d{3})+|\d+"
_RULES = []


def _rule(pattern, flags=0):
    def register(handler):
        _RULES.append((re.compile(pattern, flags), handler))
        return handler
    return register


@_rule(r"\+358[ \d]{6,}\d|\b0\d{1,3}(?:[ -]\d{2,4}){1,3}\b")
def _phone(match, following):
    digits = re.sub(r"\D", " ", match.group(0).replace("+358", "plus kolme viisi kahdeksan "))
    groups = [" ".join(ONES[int(d)] for d in group) if group.isdigit() else group for group in digits.split()]
    return ", ".join(groups)


@_rule(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})?(?!\d)")
def _date(match, following):
    day, month, year = int(match.group(1)), int(match.group(2)), match.group(3)
    if not (1 <= day <= 31 and 1 <= month <= 12):
        return None
    text = f"{ordinal(day)} {MONTHS[month - 1]}"
    return f"{text} {cardinal(int(year))}" if year else text


@_rule(r"\b(?:klo|kello)?\s*(\d{1,2})[.:](\d{2})\s*[–-]\s*(\d{1,2})[.:](\d{2})\b", re.I)
def _time_range(match, following):
    h1, m1, h2, m2 = match.groups()
    if int(h1) > 24 or int(h2) > 24 or int(m1) > 59 or int(m2) > 59:
        return None
    lead = match.group(0).lstrip()
    prefix = ("Kello " if lead[0].isupper() else "kello ") if lead.lower().startswith(("klo", "kello")) else ""
    return f"{prefix}{_time_case(h1, m1, 'elat')} {_time_case(h2, m2, 'illat')}"


@_rule(r"\b(?:klo|kello)\s*(\d{1,2})(?:[.:](\d{2}))?\b", re.I)
def _clock(match, following):
    hours, minutes = match.groups()
    if int(hours) > 24 or minutes and int(minutes) > 59:
        return None
    return f"{'Kello' if match.group(0).lstrip()[0].isupper() else 'kello'} {_time(hours, minutes)}"


@_rule(r"\b(\d{1,2})[.:](\d{2})\b(?![.,]\d)")
def _bare_time(match, following):
    hours, minutes = match.groups()
    if int(hours) > 24 or int(minutes) > 59:
        return None
    return _time(hours, minutes)


@_rule(r"\b(ma|ti|ke|to|pe|la|su)\s*[–-]\s*(ma|ti|ke|to|pe|la|su)\b")
def _weekdays(match, following):
    return f"{_WEEKDAY_FROM[match.group(1)]} {_WEEKDAY_TO[match.group(2)]}"


@_rule(r"(?<![\d,.])(" + _NUM + r")\s*[–-]\s*(" + _NUM + r")(?![\d,])")
def _range(match, following):
    return f"{cardinal(_number(match.group(1)), 'elat')} {cardinal(_number(match.group(2)), 'illat')}"


@_rule(r"(-?)(?<![\d,])(" + _NUM + r")(?:,(\d+))?\s*(€|snt|%|km|cm|mm|kg|dl|min|°C|°|kpl|[mglhse])(?![\w])")
def _with_unit(match, following):
    sign, whole, fraction, unit = match.groups()
    return ("miinus " if sign else "") + _amount(whole, fraction, unit)


@_rule(r"(?<![\d,.])(\d{1,4})\.(?=\s+\w)")
def _ordinal(match, following):
    if following and following[0].isupper():
        return None  # a number at the end of a sentence
    return ordinal(int(match.group(1)), case_of(following, ordinal=True) if following else "nom")


@_rule(r"(-?)(?<![\d,])(" + _NUM + r"),(\d+)(?![\d,])")
def _decimal(match, following):
    sign, whole, fraction = match.groups()
    return ("miinus " if sign else "") + decimal(whole, fraction)


@_rule(r"(?<![\d,.])(\d+):(\w+)")
def _with_suffix(match, following):
    case = suffix_case(match.group(2))
    return cardinal(int(match.group(1)), case) if case else None


@_rule(r"(?<![\d,.])(" + _NUM + r")(?![\d,:])")
def _plain(match, following):
    case = case_of(following) if following else "nom"
    return cardinal(_number(match.group(1)), case)


def speak_finnish(text):
    """Text with numbers, times, prices, dates and abbreviations written out in words."""
    def expand(match):
        word = ABBREVIATIONS[match.group(0).lower()]
        return word.capitalize() if match.group(0)[0].isupper() else word

    text = re.sub(r"\b(esim|mm|jne|ns|ym|yms|tms|ks|vrt|ts|puh|os)\.", expand, text, flags=re.I)
    text = re.sub(r"\b[nN]\.(?=\s*\d)", lambda m: "Noin" if m.group(0)[0] == "N" else "noin", text)
    for pattern, handler in _RULES:
        out, last = [], 0
        for match in pattern.finditer(text):
            following = re.match(r"\s*([^\s]+)", text[match.end():])
            spoken = handler(match, following.group(1) if following else "")
            if spoken is None:
                continue
            out.append(text[last:match.start()])
            # keep a space that the match swallowed at its start
            out.append((" " if match.group(0)[:1].isspace() else "") + spoken)
            last = match.end()
        text = "".join(out) + text[last:]
    text = re.sub(r"\b[kK]lo\b", lambda m: "Kello" if m.group(0)[0] == "K" else "kello", text)
    return re.sub(r"[ \t]{2,}", " ", text)


# ---------------------------------------------------------------------- sentences

ABBREVIATIONS_BY_LANGUAGE = {
    "fi": {"esim", "mm", "jne", "ns", "n", "ym", "yms", "tms", "ks", "vrt", "ts", "puh", "os", "s", "v", "klo", "ko",
           "huom", "nk", "ap", "ip", "ma", "ti", "ke", "to", "pe", "la", "su", "tri", "prof", "rva", "hra", "nti"},
    "sv": {"t.ex", "bl.a", "osv", "ca", "kl", "s.k", "dvs", "m.fl", "f.d", "nr", "jfr", "st"},
    "en": {"mr", "mrs", "ms", "dr", "prof", "st", "e.g", "i.e", "etc", "vs", "no", "approx", "jr", "sr"},
    "de": {"z.b", "bzw", "usw", "ca", "nr", "dr", "u.a", "d.h", "s"},
}
_END = re.compile(r"([.!?…]+[\"'”’)\]]*)(\s+)")


def split_sentences(text, language="fi", dictation=False, max_characters=300):
    """Sentences of text as a list; with dictation, each sentence cut at its commas."""
    known = ABBREVIATIONS_BY_LANGUAGE.get(language, set()) | ABBREVIATIONS_BY_LANGUAGE["en"]
    sentences = []
    for paragraph in re.split(r"\n\s*\n|\n(?=\s*[-*#]|\s*\d+[.)]\s)", text):
        paragraph = re.sub(r"^\s*(?:#+|[-*]|\d+[.)])\s+", "", paragraph.strip())
        paragraph = " ".join(paragraph.split())
        start = 0
        for match in _END.finditer(paragraph):
            before = paragraph[start:match.start()].split()
            word = before[-1].lower().rstrip(".") if before else ""
            after = paragraph[match.end():match.end() + 1]
            if match.group(1).startswith(".") and len(match.group(1)) == 1:
                if word in known or re.fullmatch(r"\w", word) and not word.isdigit():
                    continue  # an abbreviation or an initial
                if word.isdigit() and after and not after.isupper():
                    continue  # an ordinal such as 3. kerros
            if after and not (after.isupper() or after.isdigit() or after in "\"'“”‘’(-–—«"):
                continue
            sentences.append(paragraph[start:match.end()].strip())
            start = match.end()
        if paragraph[start:].strip():
            sentences.append(paragraph[start:].strip())
    out = []
    for sentence in sentences:
        pieces = re.split(r"(?<=[,;:])\s+", sentence) if dictation else [sentence]
        for piece in pieces:
            out.extend(_limit(piece, max_characters))
    if dictation:  # a chunk of one or two words is read together with the next
        merged = []
        for piece in out:
            if merged and len(merged[-1].split()) < 3 and not merged[-1].endswith((".", "!", "?")):
                merged[-1] = f"{merged[-1]} {piece}"
            else:
                merged.append(piece)
        out = merged
    return out


def _limit(text, max_characters):
    """Cut text longer than max_characters at a comma or semicolon, else at the space nearest the middle."""
    if len(text) <= max_characters:
        return [text]
    cuts = [m.end() for m in re.finditer(r"[,;:]\s", text)]
    middle = len(text) / 2
    cut = min(cuts, key=lambda c: abs(c - middle)) if cuts else None
    if cut is None or not 0.2 < cut / len(text) < 0.8:
        spaces = [m.start() for m in re.finditer(r"\s", text)]
        if not spaces:
            return [text]
        cut = min(spaces, key=lambda c: abs(c - middle))
    return _limit(text[:cut].strip(), max_characters) + _limit(text[cut:].strip(), max_characters)


LANGUAGE_CODES = {
    "albanian": "sq", "arabic": "ar", "chinese": "zh", "danish": "da", "dutch": "nl", "english": "en",
    "estonian": "et", "finnish": "fi", "french": "fr", "german": "de", "greek": "el", "hebrew": "he", "hindi": "hi",
    "italian": "it", "japanese": "ja", "korean": "ko", "kurdish": "ckb", "kurdish (sorani)": "ckb", "malay": "ms",
    "norwegian": "no", "persian": "fa", "persian (dari)": "prs", "dari": "prs", "polish": "pl", "portuguese": "pt",
    "russian": "ru", "somali": "so", "spanish": "es", "swahili": "sw", "swedish": "sv", "thai": "th",
    "tigrinya": "ti", "turkish": "tr", "ukrainian": "uk", "vietnamese": "vi", "suomi": "fi", "svenska": "sv",
}


def language_code(language):
    """'Finnish (fi)', 'Finnish' or 'fi' to fi."""
    name = language.strip().lower()
    if name in LANGUAGE_CODES:
        return LANGUAGE_CODES[name]
    match = re.search(r"\((\w+)\)\s*$", name)
    if match:
        return match.group(1)
    return name


class SplitSentences:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "text": ("STRING", {"multiline": True, "default": ""}),
                "language": ("STRING", {"default": "Finnish",
                                        "tooltip": "Name or code of the text's language; sets which abbreviations "
                                                   "do not end a sentence."}),
                "dictation": ("BOOLEAN", {"default": False,
                                          "tooltip": "Also cut each sentence at its commas, for dictation."}),
                "max_characters": ("INT", {"default": 300, "min": 40, "max": 2000,
                                           "tooltip": "Longer pieces are cut at a comma or a space. Chatterbox "
                                                      "speaks at most about 40 seconds in one go."}),
            },
        }

    RETURN_TYPES = ("STRING", "INT")
    RETURN_NAMES = ("sentences", "count")
    OUTPUT_IS_LIST = (True, False)
    FUNCTION = "split"
    CATEGORY = "text"
    DESCRIPTION = ("Cuts a text into sentences, one list item each, so a text to speech node speaks them one by "
                   "one. Abbreviations, times such as 9.30 and ordinals such as 3. do not end a sentence.")

    def split(self, text, language, dictation, max_characters):
        sentences = split_sentences(text, language_code(language), dictation, max_characters)
        if not sentences:
            raise ValueError("The text is empty.")
        return (sentences, len(sentences))


class FinnishNumbersToWords:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "text": ("STRING", {"multiline": True, "default": "Bussi lähtee klo 9.30 ja lippu maksaa 3,20 €."}),
            },
            "optional": {
                "language": ("STRING", {"forceInput": True,
                                        "tooltip": "When connected, text in any language other than Finnish "
                                                   "passes through unchanged."}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text",)
    FUNCTION = "convert"
    CATEGORY = "text"
    DESCRIPTION = ("Writes out digits, times, prices, dates, decimals, percentages, phone numbers, units and common "
                   "abbreviations in Finnish words, inflected to fit the next word, for text to speech.")

    def convert(self, text, language=None):
        if language is not None and language_code(language) != "fi":
            return (text,)
        return (speak_finnish(text),)


NODE_CLASS_MAPPINGS = {
    "SplitSentences": SplitSentences,
    "FinnishNumbersToWords": FinnishNumbersToWords,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "SplitSentences": "Split Sentences",
    "FinnishNumbersToWords": "Numbers to Words (Finnish)",
}
