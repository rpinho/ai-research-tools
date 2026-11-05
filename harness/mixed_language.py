"""Mixed-language rule for email to a recipient whose contact language is not English.

Ricardo's rule (11-05): an email that carries results to a Portuguese-speaking recipient is
written in two languages, and the shape is fixed. It opens in European Portuguese, carries its
results, numbers, methods, caveats, discussion and summary in English because English is the
science language, and closes in European Portuguese again. Attachments are English whatever
the email does. The shape holds even for a recipient who writes their own science in English:
there is no "match their language" exception.

So three things fail: an opening that is not Portuguese, a closing that is not Portuguese, and
a science paragraph that is Portuguese. The middle of the email is English by default, because
that is where the discussion and the summary live as well as the numbers; a paragraph there is
only warned about when it is plainly personal and written in English, and the shape the rule
fixes is the opening and the close.

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
STATISTIC = re.compile(r'\b[rnp]\s*[=<>]|\bp\s*[=<>]|±|%|\bCI\b|\bSD\b|\bSE\b', re.I)

# Result and method vocabulary in both languages: a paragraph that argues about a measurement.
SCIENCE_TERMS = frozenset('''
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

# Words that make a middle paragraph personal rather than part of the science: family, health,
# travel, plans, well-wishing. The middle of the email is English by default, because the rule
# puts the discussion and the summary there alongside the numbers, so a warning needs positive
# evidence that a paragraph is personal. Without this, "I agree, and I won't call it an erratum"
# -- discussion, correctly in English -- was warned for not being Portuguese (friends, 11-05).
PERSONAL_WORDS = frozenset('''
    family wife husband son daughter kids children baby holiday holidays vacation weekend
    birthday congratulations wedding health hospital trip travel visit dinner lunch coffee
    hope hopefully glad lovely great sorry miss catch wonderful summer winter christmas
    familia família esposa marido filho filha filhos miudos miúdos bebe bebé ferias férias
    feriado fim semana aniversario aniversário parabens parabéns casamento saude saúde
    hospital viagem visita jantar almoco almoço cafe café espero contente otimo ótimo
    pena saudades natal verao verão inverno
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


def is_science(paragraph):
    """True when the paragraph argues about a measurement rather than about the person.

    One stray number does not make a science paragraph: "obrigado pelos 20 minutos" is a
    personal line. The test asks for a number next to result vocabulary, two numbers, or an
    explicit statistic, so that a Portuguese personal line is never failed as science.
    """
    numbers = len(NUMBER.findall(paragraph))
    terms = sum(w in SCIENCE_TERMS for w in words(paragraph))
    return bool(STATISTIC.search(paragraph)) or numbers >= 2 or (numbers >= 1 and terms >= 1)


def is_personal(paragraph):
    """True when a middle paragraph is about the person, not about the work."""
    return any(w in PERSONAL_WORDS for w in words(paragraph))


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
                    '; keep the numbers in their own English paragraph' if is_science(paragraph) else ''))
            continue
        if len(words(paragraph)) < MIN_WORDS:
            continue
        found = language_of(paragraph)
        if is_science(paragraph):
            if found == 'pt':
                failures.append('paragraph {}: results, numbers, methods and caveats go in '
                                'English, the science language'.format(number))
        elif found == 'en' and is_personal(paragraph):
            warnings.append('paragraph {}: a personal line reads better in European '
                            'Portuguese'.format(number))
    for path in attachments:
        if language_of(attachment_text(path)) == 'pt':
            failures.append('attachment {}: attachments are always English'.format(Path(path).name))
    return failures, warnings
