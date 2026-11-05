"""Mixed-language rule for email to a recipient whose contact language is not English.

Ricardo's rule (11-05, as refined twice): an email to a Portuguese-speaking recipient is
written in Portuguese. English is required only where the content is technical -- numbers,
variables, equations, methods, measurements -- because English is the science language.
Attachments are always English. The shape holds even for a recipient who writes their own
science in English: there is no "match their language" exception.

So the opening and the closing must be Portuguese, a technical paragraph in Portuguese
fails, and a non-technical paragraph in English is warned about wherever it appears. The
middle of the email is not English by default: "a separate model re-checked it", "no human
has looked at it yet", "is this useful or is it slop?" are ordinary prose and belong in
Portuguese. An earlier version of this file had that backwards, after "discussion and
summary" was read as part of the science block.

Technical means digits beside result vocabulary, two or more numbers, a statistic, a maths
symbol, a variable-like token, or a dense run of technical terms. Term vocabulary alone is
not enough: "a separate model re-checked the result" uses two of those words and is still
ordinary prose.

Language identification here is a deliberately small offline heuristic. It counts function
words that one language has and the other does not, plus letters that English does not use.
It never touches the network and never loads a model. It answers None when a passage is too
short or too mixed to call, and callers treat None as "no finding": a heuristic that is unsure
must not block a send.
"""
from pathlib import Path
import re

# Function words with effectively no counterpart in the other language. 'do' is left out of the
# Portuguese set because it is an English verb, and 'no', 'a', 'e', 'o', 'as', 'os' are left out
# of both because they are common in each.
PT_WORDS = frozenset('''
    de da das dos nao não voce você obrigado obrigada com para pelo pela por isso isto aqui
    um uma uns umas mas que como nos nós ja já esta está estao estão sao são sou somos muito
    tambem também sobre seu sua seus suas meu minha meus minhas nosso nossa ola olá
    abraco abraço abracos abraços cumprimentos saudacoes saudações atenciosamente
    bom boa dia tarde noite porque quando onde quem tudo bem fiz fizemos temos tenho tem
    foi fomos ser estar pode poderia gostaria espero desculpa desculpe ate até breve
    agora depois antes ainda entao então assim talvez claro certo coisa vez anos
    trabalho trabalhar falar dizer fazer achei acho penso parece
'''.split())

EN_WORDS = frozenset('''
    the and of to is are was were be been being this that these those with for from
    we you your our their which would could should will have has had but not they there
    about between because when where while all any both each more most than then though
    thanks regards hello dear please sorry best kind sincerely yours
    here what who how why its it's don't doesn't very much also still again
    work working talk say said make made think thought seems looks
'''.split())

# Letters English does not use. Accented vowels appear in other languages too, which is fine:
# the comparison is only ever Portuguese against English.
PT_LETTERS = frozenset('ãõçáâàéêíóôúü')

WORD = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*", re.UNICODE)

# A number that carries quantity, not a date or a list marker.
NUMBER = re.compile(r'(?<![\w/-])[-+]?\d+(?:[.,]\d+)?(?![\w/-])')
STATISTIC = re.compile(r'\b[rnp]\s*[=<>]|±|%', re.I)
# Case matters for these, and only for these: under re.I the Portuguese conjunction "se" --
# "ver se conseguem", "só se te apetecer" -- matched \bSE\b, so any Portuguese paragraph
# using it was read as a result and failed for not being in English (friends, 11-05).
ACRONYM = re.compile(r'\b(?:CI|SD|SE|SEM|IQR|AUC|RMSE|MAE)\b')

# A maths symbol, and a token carrying both letters and digits or an underscore: F054, k_eff,
# run11, 10x. These make a paragraph technical on their own.
MATH = re.compile(r'[=<>\u2264\u2265\u00b1\u00d7\u00f7\u2211\u221a\u2248\u2260]|\^\d|\$[^$\n]+\$')
VARIABLE = re.compile(r'\b(?=\w*[^\W\d_])(?=\w*\d)\w{2,}\b|\b[^\W\d_]\w*_\w+\b')

# Result and method vocabulary in both languages: a paragraph that argues about a measurement.
TECHNICAL_TERMS = frozenset('''
    baseline control controls correlation dataset data estimate interval confidence
    measured measurement method methods median mean sample samples significance significant
    threshold replicate replicates predictor variance deviation noise spread ranking rank
    model models score scores metric metrics caveat caveats limitation limitations
    result results finding findings evidence hypothesis test tested holdout ablation
    medimos medida medidas metodo método metodos métodos resultado resultados
    media média mediana correlacao correlação amostra amostras controlo controle
    limiar intervalo confianca confiança desvio erro ruido ruído dados variancia variância
    hipotese hipótese ressalva ressalvas limitacao limitação conclusao conclusão
'''.split())

MIN_WORDS = 4  # Below this a paragraph is a salutation or a link, not prose to judge.

# Openings and closings are short, so word counting often cannot call them. These settle the
# common ones outright: "Hi Alpha," is English and "Até breve" is Portuguese, four words or not.
EDGE_EN = frozenset('''hi hello hey dear thanks thank regards best cheers sincerely
                       yours morning afternoon evening'''.split())
EDGE_PT = frozenset('''ola olá viva bom boa dia tarde noite obrigado obrigada abraco abraço
                       abracos abraços cumprimentos saudacoes saudações atenciosamente
                       ate até breve beijinhos'''.split())


def words(text):
    return [w.lower() for w in WORD.findall(text)]


def language_of(text):
    """'pt', 'en', or None when the passage is too short or too mixed to call."""
    found = words(text)
    pt = sum(w in PT_WORDS for w in found)
    pt += sum(1 for w in found if PT_LETTERS & set(w))
    en = sum(w in EN_WORDS for w in found)
    best, other = max(pt, en), min(pt, en)
    if best < 2 or best - other < 2:
        return None
    return 'pt' if pt > en else 'en'


def is_technical(paragraph):
    """True when the paragraph carries the measurement rather than talks about the work.

    One stray number does not make it technical: "obrigado pelos 20 minutos" is an ordinary
    line. Neither does the vocabulary on its own, or "a separate model re-checked the result"
    would be technical when it is plain prose (Ricardo's example, 11-05). It takes a number
    beside that vocabulary, two numbers, a statistic, a maths symbol, a variable-like token,
    or a dense run of terms.
    """
    found = words(paragraph)
    numbers = len(NUMBER.findall(paragraph))
    terms = sum(w in TECHNICAL_TERMS for w in found)
    if STATISTIC.search(paragraph) or ACRONYM.search(paragraph):
        return True
    if MATH.search(paragraph) or VARIABLE.search(paragraph):
        return True
    if numbers >= 2 or (numbers >= 1 and terms >= 1):
        return True
    return terms >= 3 and terms / max(len(found), 1) >= 0.12


def paragraphs(text):
    return [p for p in re.split(r'\n\s*\n', text.strip()) if p.strip()]


def code(language):
    """Normalise a contact language field: 'pt-PT' and 'PT' are Portuguese; '' is English."""
    if not language:
        return None
    value = str(language).strip().lower().replace('_', '-').split('-')[0]
    if not re.fullmatch(r'[a-z]{2,3}', value):
        raise ValueError('contact language must be a language code such as pt')
    return None if value == 'en' else value


def attachment_text(path):
    try:
        if Path(path).suffix.lower() not in ('.md', '.txt', '.csv', '.tsv'):
            return ''
        return Path(path).read_text(errors='replace')
    except OSError:
        return ''


def edge_language(text):
    """Language of an opening or closing, deciding on greeting words before word counts."""
    found = set(words(text))
    pt, en = len(found & EDGE_PT), len(found & EDGE_EN)
    if pt != en:
        return 'pt' if pt > en else 'en'
    return language_of(text)


def findings(text, language, attachments=()):
    """Return (failures, warnings) for the mixed-language rule.

    Positions are reported, never the text itself: a lint result is written to the lane ledger
    and the draft body stays a private file.
    """
    if code(language) != 'pt':
        return [], []
    failures, warnings = [], []
    blocks = paragraphs(text)
    edges = {0: 'opening', len(blocks) - 1: 'closing'} if blocks else {}
    for index, paragraph in enumerate(blocks):
        number, role = index + 1, edges.get(index)
        if role:
            # The shape is fixed: this email opens and closes in European Portuguese.
            if edge_language(paragraph) == 'en':
                failures.append('paragraph {}: the {} must be in European Portuguese{}'.format(
                    number, 'opening' if index == 0 else 'closing',
                    '; keep the numbers in their own English paragraph'
                    if is_technical(paragraph) else ''))
            continue
        if len(words(paragraph)) < MIN_WORDS:
            continue
        found = language_of(paragraph)
        if is_technical(paragraph):
            if found == 'pt':
                failures.append('paragraph {}: numbers, variables, methods and results go in '
                                'English, the science language'.format(number))
        elif found == 'en':
            warnings.append('paragraph {}: nothing technical here, so it reads better in '
                            'European Portuguese'.format(number))
    for path in attachments:
        if language_of(attachment_text(path)) == 'pt':
            failures.append('attachment {}: attachments are always English'.format(Path(path).name))
    return failures, warnings
