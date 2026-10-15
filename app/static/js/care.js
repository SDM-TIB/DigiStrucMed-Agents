// Patient-facing texts and helpers: what evidence grades mean, the safety note under answers, the triage banners.
// Which banner shows is decided on the server by a System One model (app/triage.py); the words are fixed here.
// Every sentence here is fixed and written by hand, in the five languages of the agents. Nothing comes from a model,
// so these parts of the screen can never invent a fact. Grade meanings follow the ESC and ACC/AHA definitions.
window.care = (() => {
  const LANGS = ['en', 'de', 'es', 'ru', 'hi'];
  let lang = 'en';

  // class of recommendation. tone drives the colour: III (do not) is red, never the accent of a recommendation
  const CLASS = {
    I: {tone: 'yes', en: ['Recommended', 'Evidence or general agreement that it helps and works.'],
        de: ['Empfohlen', 'Belege oder breite Übereinstimmung, dass es nützt und wirkt.'],
        es: ['Recomendado', 'Evidencia o acuerdo general de que es beneficioso y eficaz.'],
        ru: ['Рекомендуется', 'Доказано или общепризнано, что это полезно и эффективно.'],
        hi: ['अनुशंसित', 'प्रमाण या सामान्य सहमति कि यह लाभकारी और प्रभावी है।']},
    IIa: {tone: 'lean', en: ['Should be considered', 'The evidence or expert opinion leans towards it being useful.'],
          de: ['Sollte erwogen werden', 'Belege oder Expertenmeinung sprechen eher für den Nutzen.'],
          es: ['Debe considerarse', 'La evidencia o la opinión de expertos se inclina a favor de su utilidad.'],
          ru: ['Следует рассмотреть', 'Доказательства или мнение экспертов скорее говорят в пользу пользы.'],
          hi: ['विचार किया जाना चाहिए', 'प्रमाण या विशेषज्ञ राय इसके उपयोगी होने की ओर झुकी है।']},
    IIb: {tone: 'maybe', en: ['May be considered', 'Whether it helps is less well established.'],
          de: ['Kann erwogen werden', 'Ob es nützt, ist weniger gut belegt.'],
          es: ['Puede considerarse', 'Su utilidad está menos establecida.'],
          ru: ['Можно рассмотреть', 'Польза установлена менее надёжно.'],
          hi: ['विचार किया जा सकता है', 'इसकी उपयोगिता कम स्पष्ट रूप से स्थापित है।']},
    III: {tone: 'no', en: ['Not recommended', 'Evidence or agreement that it does not help and may even harm.'],
          de: ['Nicht empfohlen', 'Belege oder Übereinstimmung, dass es nicht nützt und sogar schaden kann.'],
          es: ['No recomendado', 'Evidencia o acuerdo de que no es útil y puede incluso ser perjudicial.'],
          ru: ['Не рекомендуется', 'Доказано или общепризнано, что это не помогает и может даже навредить.'],
          hi: ['अनुशंसित नहीं', 'प्रमाण या सहमति कि यह उपयोगी नहीं है और नुकसान भी कर सकता है।']},
  };
  const AHA = {'1': 'I', '2a': 'IIa', '2b': 'IIb', '3': 'III'};
  // level of evidence: how strong the studies behind the recommendation are
  const LEVEL = {
    A: {en: 'Strong evidence: several randomised trials or meta-analyses.', de: 'Starke Belege: mehrere randomisierte Studien oder Metaanalysen.',
        es: 'Evidencia sólida: varios ensayos aleatorizados o metaanálisis.', ru: 'Сильные доказательства: несколько рандомизированных исследований или метаанализы.',
        hi: 'मज़बूत प्रमाण: कई यादृच्छिक परीक्षण या मेटा-विश्लेषण।'},
    B: {en: 'Moderate evidence: one randomised trial or large non-randomised studies.', de: 'Mittlere Belege: eine randomisierte Studie oder große nicht randomisierte Studien.',
        es: 'Evidencia moderada: un ensayo aleatorizado o grandes estudios no aleatorizados.', ru: 'Умеренные доказательства: одно рандомизированное или крупные нерандомизированные исследования.',
        hi: 'मध्यम प्रमाण: एक यादृच्छिक परीक्षण या बड़े गैर-यादृच्छिक अध्ययन।'},
    'B-R': {en: 'Moderate evidence from randomised studies.', de: 'Mittlere Belege aus randomisierten Studien.', es: 'Evidencia moderada de estudios aleatorizados.',
            ru: 'Умеренные доказательства из рандомизированных исследований.', hi: 'यादृच्छिक अध्ययनों से मध्यम प्रमाण।'},
    'B-NR': {en: 'Moderate evidence from non-randomised studies.', de: 'Mittlere Belege aus nicht randomisierten Studien.', es: 'Evidencia moderada de estudios no aleatorizados.',
             ru: 'Умеренные доказательства из нерандомизированных исследований.', hi: 'गैर-यादृच्छिक अध्ययनों से मध्यम प्रमाण।'},
    C: {en: 'Limited evidence: expert consensus and/or small studies.', de: 'Begrenzte Belege: Expertenkonsens und/oder kleine Studien.',
        es: 'Evidencia limitada: consenso de expertos y/o estudios pequeños.', ru: 'Ограниченные доказательства: консенсус экспертов и/или небольшие исследования.',
        hi: 'सीमित प्रमाण: विशेषज्ञों की सहमति और/या छोटे अध्ययन।'},
    'C-LD': {en: 'Limited evidence: few or small studies.', de: 'Begrenzte Belege: wenige oder kleine Studien.', es: 'Evidencia limitada: pocos estudios o pequeños.',
             ru: 'Ограниченные доказательства: мало или небольшие исследования.', hi: 'सीमित प्रमाण: कम या छोटे अध्ययन।'},
    'C-EO': {en: 'Based on expert opinion.', de: 'Beruht auf Expertenmeinung.', es: 'Basado en la opinión de expertos.', ru: 'Основано на мнении экспертов.', hi: 'विशेषज्ञ राय पर आधारित।'},
  };

  const T = {
    whatMeans: {en: 'What does this mean?', de: 'Was bedeutet das?', es: '¿Qué significa?', ru: 'Что это значит?', hi: 'इसका क्या मतलब है?'},
    class: {en: 'Class', de: 'Klasse', es: 'Clase', ru: 'Класс', hi: 'श्रेणी'},
    level: {en: 'Level', de: 'Evidenz', es: 'Nivel', ru: 'Уровень', hi: 'स्तर'},
    note: {en: 'From clinical guidelines, to help you understand. Please talk to your care team before changing any treatment.',
           de: 'Aus klinischen Leitlinien, zum Verstehen. Sprechen Sie bitte mit Ihrem Behandlungsteam, bevor Sie etwas an Ihrer Behandlung ändern.',
           es: 'De guías clínicas, para ayudarle a entender. Hable con su equipo médico antes de cambiar cualquier tratamiento.',
           ru: 'Из клинических рекомендаций, для понимания. Перед любыми изменениями в лечении поговорите со своим лечащим врачом.',
           hi: 'क्लिनिकल दिशानिर्देशों से, समझने में मदद के लिए। इलाज में कोई भी बदलाव करने से पहले अपनी देखभाल टीम से बात करें।'},
    noteLino: {en: 'Lino answers from memory with a small fine-tuned model. Not checked against the guidelines: compare with Luna or Guido, and talk to your care team.',
               de: 'Lino antwortet aus dem Gedächtnis eines kleinen, feinjustierten Modells. Nicht gegen die Leitlinien geprüft: vergleichen Sie mit Luna oder Guido und sprechen Sie mit Ihrem Behandlungsteam.',
               es: 'Lino responde de memoria con un modelo pequeño ajustado. No verificado con las guías: compárelo con Luna o Guido y hable con su equipo médico.',
               ru: 'Лино отвечает по памяти небольшой дообученной модели. Не проверено по рекомендациям: сравните с Луной или Гвидо и поговорите с врачом.',
               hi: 'लीनो एक छोटे फ़ाइन-ट्यून्ड मॉडल की स्मृति से उत्तर देता है। दिशानिर्देशों से जाँचा नहीं गया: लूना या गुइडो से तुलना करें और अपनी देखभाल टीम से बात करें।'},
    unverified: {en: 'From memory · not checked against guidelines', de: 'Aus dem Gedächtnis · nicht gegen Leitlinien geprüft',
                 es: 'De memoria · no verificado con las guías', ru: 'По памяти · не проверено по рекомендациям', hi: 'स्मृति से · दिशानिर्देशों से जाँचा नहीं गया'},
    summary: {en: 'Summary', de: 'Zusammenfassung', es: 'Resumen', ru: 'Кратко', hi: 'सारांश'},
    guideline: {en: 'Guideline text, word for word', de: 'Leitlinientext, wörtlich', es: 'Texto de la guía, literal', ru: 'Текст рекомендации дословно', hi: 'दिशानिर्देश का पाठ, शब्दशः'},
    alertTitle: {en: 'Is this happening right now?', de: 'Passiert das gerade jetzt?', es: '¿Le está pasando ahora mismo?', ru: 'Это происходит прямо сейчас?', hi: 'क्या यह अभी हो रहा है?'},
    alert: {en: 'Severe or sudden chest pain, breathlessness or fainting is an emergency. Call 112 now (emergency number in the EU, Russia and India) or your local emergency number. Do not wait for an answer here.',
            de: 'Starke oder plötzliche Brustschmerzen, Atemnot oder Ohnmacht sind ein Notfall. Rufen Sie jetzt die 112 an. Warten Sie nicht auf eine Antwort hier.',
            es: 'El dolor de pecho fuerte o repentino, la falta de aire o el desmayo son una urgencia. Llame ahora al 112 o a su número local de emergencias. No espere una respuesta aquí.',
            ru: 'Сильная или внезапная боль в груди, одышка или обморок — это неотложное состояние. Позвоните сейчас 112 или по местному номеру экстренной помощи. Не ждите ответа здесь.',
            hi: 'तेज़ या अचानक सीने में दर्द, साँस फूलना या बेहोशी आपात स्थिति है। अभी 112 पर कॉल करें या अपने स्थानीय आपातकालीन नंबर पर। यहाँ जवाब का इंतज़ार न करें।'},
    call: {en: 'Call 112', de: '112 anrufen', es: 'Llamar al 112', ru: 'Позвонить 112', hi: '112 पर कॉल करें'},
    urgentTitle: {en: 'Contact your care team today', de: 'Melden Sie sich heute bei Ihrem Behandlungsteam', es: 'Contacte hoy con su equipo médico',
                  ru: 'Свяжитесь с лечащим врачом сегодня', hi: 'आज ही अपनी देखभाल टीम से संपर्क करें'},
    urgent: {en: 'Gaining about 2 kg within a few days, more swelling or getting more breathless can be signs of fluid build-up in heart failure. Please call your doctor or heart failure nurse today. If it gets suddenly worse, call 112.',
             de: 'Etwa 2 kg mehr innerhalb weniger Tage, mehr Schwellungen oder mehr Atemnot können bei Herzinsuffizienz Zeichen von Wassereinlagerung sein. Rufen Sie bitte heute Ihre Ärztin, Ihren Arzt oder Ihre Herzinsuffizienz-Pflegekraft an. Wird es plötzlich schlimmer, rufen Sie die 112.',
             es: 'Ganar unos 2 kg en pocos días, más hinchazón o más falta de aire pueden ser signos de retención de líquidos en la insuficiencia cardíaca. Llame hoy a su médico o enfermera. Si empeora de repente, llame al 112.',
             ru: 'Прибавка около 2 кг за несколько дней, усиление отёков или одышки могут быть признаками задержки жидкости при сердечной недостаточности. Позвоните сегодня своему врачу. Если станет резко хуже, звоните 112.',
             hi: 'कुछ दिनों में लगभग 2 किलो वज़न बढ़ना, ज़्यादा सूजन या साँस फूलना हार्ट फ़ेल्योर में शरीर में पानी जमा होने के संकेत हो सकते हैं। कृपया आज ही अपने डॉक्टर से बात करें। अगर अचानक हालत बिगड़े, तो 112 पर कॉल करें।'},
    supportTitle: {en: 'You don\'t have to carry this alone', de: 'Sie müssen das nicht allein tragen', es: 'No tiene que pasar por esto solo',
                   ru: 'Вы не обязаны справляться с этим в одиночку', hi: 'आपको यह अकेले नहीं सहना है'},
    support: {en: 'If you are thinking about harming yourself, please talk to someone now: call 112 in an emergency, or reach out to a person you trust or your doctor.',
              de: 'Wenn Sie daran denken, sich etwas anzutun, sprechen Sie bitte jetzt mit jemandem: im Notfall 112, oder rund um die Uhr kostenlos die Telefonseelsorge unter 0800 111 0 111, oder ein Mensch, dem Sie vertrauen.',
              es: 'Si está pensando en hacerse daño, hable ahora con alguien: llame al 112 en una emergencia, o a una persona de confianza o a su médico.',
              ru: 'Если вы думаете о том, чтобы причинить себе вред, пожалуйста, поговорите с кем-нибудь сейчас: в экстренной ситуации звоните 112, или обратитесь к близкому человеку или врачу.',
              hi: 'अगर आप ख़ुद को नुकसान पहुँचाने के बारे में सोच रहे हैं, तो कृपया अभी किसी से बात करें: आपात स्थिति में 112 पर कॉल करें, या किसी भरोसेमंद व्यक्ति या अपने डॉक्टर से बात करें।'},
    checkTitle: {en: 'Guideline check of Lino\'s answer', de: 'Leitlinien-Check von Linos Antwort', es: 'Revisión de la respuesta de Lino con las guías',
                  ru: 'Проверка ответа Лино по рекомендациям', hi: 'लीनो के उत्तर की दिशानिर्देश जाँच'},
    checking: {en: 'Checking each statement against the guidelines…', de: 'Jede Aussage wird mit den Leitlinien abgeglichen…', es: 'Comparando cada afirmación con las guías…',
               ru: 'Сверяем каждое утверждение с рекомендациями…', hi: 'हर कथन को दिशानिर्देशों से मिलाया जा रहा है…'},
    checkNone: {en: 'No medical statements to check.', de: 'Keine medizinischen Aussagen zum Prüfen.', es: 'No hay afirmaciones médicas que revisar.',
                ru: 'Нет медицинских утверждений для проверки.', hi: 'जाँचने के लिए कोई चिकित्सीय कथन नहीं।'},
    checkHow: {en: 'Each statement was looked up in the guideline library and judged by a classifier (Jev). The guideline text is shown word for word.',
               de: 'Jede Aussage wurde in der Leitlinien-Bibliothek gesucht und von einem Klassifikator (Jev) bewertet. Der Leitlinientext steht wörtlich da.',
               es: 'Cada afirmación se buscó en la biblioteca de guías y la evaluó un clasificador (Jev). El texto de la guía se muestra literalmente.',
               ru: 'Каждое утверждение найдено в библиотеке рекомендаций и оценено классификатором (Jev). Текст рекомендации приведён дословно.',
               hi: 'हर कथन को दिशानिर्देश पुस्तकालय में खोजा गया और एक क्लासिफ़ायर (Jev) ने आँका। दिशानिर्देश का पाठ शब्दशः दिखाया गया है।'},
    supported: {en: 'Supported by the guidelines', de: 'Von den Leitlinien gestützt', es: 'Respaldado por las guías', ru: 'Подтверждается рекомендациями', hi: 'दिशानिर्देशों से समर्थित'},
    contradicted: {en: 'Contradicts the guidelines', de: 'Widerspricht den Leitlinien', es: 'Contradice las guías', ru: 'Противоречит рекомендациям', hi: 'दिशानिर्देशों के विपरीत'},
    not_addressed: {en: 'Not found in the guidelines', de: 'In den Leitlinien nicht gefunden', es: 'No encontrado en las guías', ru: 'Не найдено в рекомендациях', hi: 'दिशानिर्देशों में नहीं मिला'},
    unclear: {en: 'Unclear', de: 'Unklar', es: 'No está claro', ru: 'Неясно', hi: 'अस्पष्ट'},
    openPage: {en: 'Open page', de: 'Seite öffnen', es: 'Abrir página', ru: 'Открыть страницу', hi: 'पृष्ठ खोलें'},
    askNext: {en: 'Ask next', de: 'Weiterfragen', es: 'Preguntar después', ru: 'Спросить дальше', hi: 'आगे पूछें'},
    forDoctor: {en: 'For your doctor', de: 'Für Ihr Arztgespräch', es: 'Para su médico', ru: 'Для разговора с врачом', hi: 'अपने डॉक्टर के लिए'},
    addToList: {en: 'Add to my questions', de: 'Zu meinen Fragen', es: 'Añadir a mis preguntas', ru: 'Добавить в мои вопросы', hi: 'मेरे प्रश्नों में जोड़ें'},
    myQuestions: {en: 'My questions for the doctor', de: 'Meine Fragen für den Arzt', es: 'Mis preguntas para el médico', ru: 'Мои вопросы врачу', hi: 'डॉक्टर के लिए मेरे प्रश्न'},
    noQuestions: {en: 'Tap a question under an answer to collect it here.', de: 'Tippen Sie auf eine Frage unter einer Antwort, um sie hier zu sammeln.',
                  es: 'Toque una pregunta bajo una respuesta para guardarla aquí.', ru: 'Нажмите на вопрос под ответом, чтобы собрать его здесь.', hi: 'किसी उत्तर के नीचे दिए प्रश्न पर टैप करें, वह यहाँ जुड़ जाएगा।'},
    copy: {en: 'Copy all', de: 'Alle kopieren', es: 'Copiar todo', ru: 'Скопировать все', hi: 'सब कॉपी करें'},
    copied: {en: 'Copied', de: 'Kopiert', es: 'Copiado', ru: 'Скопировано', hi: 'कॉपी हो गया'},
    clear: {en: 'Clear', de: 'Leeren', es: 'Vaciar', ru: 'Очистить', hi: 'साफ़ करें'},
    added: {en: 'Added to your questions for the doctor', de: 'Zu Ihren Fragen für den Arzt hinzugefügt', es: 'Añadida a sus preguntas para el médico',
            ru: 'Добавлено в ваши вопросы врачу', hi: 'डॉक्टर के लिए आपके प्रश्नों में जोड़ा गया'},
    uncited: {en: 'No guideline source is cited for this part.', de: 'Für diesen Teil ist keine Leitlinienquelle angegeben.', es: 'Esta parte no cita ninguna guía.',
              ru: 'Для этой части не указан источник.', hi: 'इस हिस्से के लिए कोई दिशानिर्देश स्रोत नहीं दिया गया।'},
    youCouldAsk: {en: 'You could ask', de: 'Sie könnten fragen', es: 'Podría preguntar', ru: 'Можно спросить', hi: 'आप पूछ सकते हैं'},
    askAgent: {en: 'Ask {name}…', de: '{name} fragen…', es: 'Preguntar a {name}…', ru: 'Спросить {name}…', hi: '{name} से पूछें…'},
    askLuna: {en: 'Ask Luna about heart guidelines…', de: 'Fragen Sie Luna zu den Herz-Leitlinien…', es: 'Pregunte a Luna sobre las guías cardíacas…',
              ru: 'Спросите Луну о кардиологических рекомендациях…', hi: 'लूना से हृदय दिशानिर्देशों के बारे में पूछें…'},
    searching: {en: 'Searching guidelines for', de: 'Durchsucht die Leitlinien nach', es: 'Buscando en las guías', ru: 'Поиск в рекомендациях:', hi: 'दिशानिर्देशों में खोज:'},
    readingFull: {en: 'Reading the full page', de: 'Liest die ganze Seite', es: 'Leyendo la página completa', ru: 'Читает страницу целиком', hi: 'पूरा पृष्ठ पढ़ रहे हैं'},
    reading: {en: 'Reading {ref}, page {page}', de: 'Liest {ref}, Seite {page}', es: 'Leyendo {ref}, página {page}', ru: 'Читает {ref}, стр. {page}', hi: '{ref} पढ़ रहे हैं, पृष्ठ {page}'},
    pages: {en: 'pages', de: 'Seiten', es: 'páginas', ru: 'стр.', hi: 'पृष्ठ'},
    talk: {en: 'Call 112', de: '112 anrufen', es: 'Llamar al 112', ru: 'Позвонить 112', hi: '112 पर कॉल करें'},
  };

  // symptoms that can mean an emergency right now, in the five languages. a match shows the banner, it never blocks
  const RED = new RegExp([
    // en
    'chest pain', 'pain in (my|the) chest', 'crushing chest', 'tight(ness)? in (my|the) chest', "can'?t breathe", 'cannot breathe', 'struggling to breathe',
    'gasping', 'faint(ed|ing)', 'passed out', 'unconscious', 'collapsed', 'cough(ing)? (up )?(blood|pink)', 'slurred speech',
    // de
    'brustschmerz', 'schmerzen? in der brust', 'druck auf der brust', 'engegefühl in der brust', 'bekomme (kaum|keine) luft', 'keine luft mehr',
    '(starke|plötzliche|akute) atemnot', 'atemnot in ruhe', 'ohnmächtig', 'bewusstlos', 'zusammengebrochen', 'blut (ge)?hust',
    // es
    'dolor (en el |de )?pecho', 'opresión en el pecho', 'no puedo respirar', 'me ahogo', 'me desmay', 'inconsciente', 'tos con sangre',
    // ru
    'бол(ь|и|ит) в груди', 'давит в груди', 'не могу дышать', 'задыхаюсь', 'потерял(а)? сознани', 'без сознания', 'кашл\S* кровью',
    // hi
    'सीने में (तेज़ )?दर्द', 'छाती में दर्द', 'सांस नहीं (आ|ले)', 'साँस नहीं (आ|ले)', 'बेहोश', 'खून की खांसी',
  ].join('|'), 'i');

  const t = (key, l = lang) => (T[key] || {})[l] || (T[key] || {}).en || '';
  const pick = row => row[lang] || row.en;

  // "Class I · LoE A", "Class 2a/2b · LoE B-NR", "IIa" ... -> {cls: ['I'], loe: ['A']}
  function parse(grade) {
    const s = String(grade || '');
    const cm = s.match(/class\s*([0-9IVab/ ]+?)(?=\s*(?:·|$|loe|level))/i), lm = s.match(/(?:loe|level)\s*([A-C](?:-[A-Z]{1,2})?(?:\s*\/\s*[A-C](?:-[A-Z]{1,2})?)*)/i);
    const cls = cm ? cm[1].split('/').map(x => x.trim()).map(x => AHA[x.toLowerCase()] || x).filter(x => CLASS[x]) : [];
    const loe = lm ? lm[1].split('/').map(x => x.trim().toUpperCase()).filter(x => LEVEL[x]) : [];
    return {cls, loe};
  }

  // the grade as a disclosure: a coloured plain-language tag, the original notation, and on tap what both mean.
  // h is json-render's element factory (react or the plain-dom fallback), so the same markup works in both
  function grade(h, raw) {
    const {cls, loe} = parse(raw);
    if (!cls.length && !loe.length) return raw ? h('span', {className: 'grade'}, raw) : null;
    const main = cls.length ? CLASS[cls[cls.length - 1]] : null;  // a range like IIa/IIb: the weaker one leads, it is the honest one
    const tag = main ? pick(main)[0] : t('level') + ' ' + loe.join('/');
    const rows = [...cls.map(c => h('li', {key: 'c' + c}, h('b', null, `${t('class')} ${c}: ${pick(CLASS[c])[0]}. `), pick(CLASS[c])[1])),
                  ...loe.map(l => h('li', {key: 'l' + l}, h('b', null, `${t('level')} ${l}: `), pick(LEVEL[l])))];
    return h('details', {className: 'gx tone-' + (main ? main.tone : 'none')},
      h('summary', {title: t('whatMeans')}, h('span', {className: 'gx-tag'}, tag), h('span', {className: 'gx-raw'}, raw), h('span', {className: 'gx-i', 'aria-hidden': 'true'}, 'i')),
      h('ul', null, rows));
  }

  return {
    LANGS, t, grade, parse,
    get lang() { return lang; },
    setLang(l) { if (!LANGS.includes(l) || l === lang) return; lang = l; document.documentElement.lang = l; window.dispatchEvent(new Event('care-lang')); },
    fill: (key, vars, l) => t(key, l).replace(/\{(\w+)\}/g, (_, k) => vars[k] ?? ''),
    redFlag: text => RED.test(String(text || '')),  // only when the triage model cannot be reached: a missed emergency is worse
  };
})();
