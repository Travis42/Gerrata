"""US/UK English spelling pairs for false positive filtering.

When comparing a Project Gutenberg text (often Americanized) against a British
first edition scan, these pairs represent intentional spelling normalization,
NOT transcription errors. Both spellings are correct English — neither is a
scanno.

The filter checks whether (pg_text, scan_text) stripped of punctuation is a
known US/UK pair in either direction.

Coverage: ~300 pairs covering common and obscure variants.
Sources: Oxford English Dictionary, Wikipedia "American and British English
spelling differences", observed patterns in PG texts vs British first editions.
"""

from __future__ import annotations

# Format: (us_spelling, uk_spelling)
# Both forms are correct — filtering these removes edition normalization noise.
US_UK_SPELLING_PAIRS: set[tuple[str, str]] = {
    # -our / -or
    ("honor", "honour"),
    ("honors", "honours"),
    ("honored", "honoured"),
    ("honorable", "honourable"),
    ("honorably", "honourably"),
    ("dishonor", "dishonour"),
    ("dishonorable", "dishonourable"),
    ("dishonoured", "dishonored"),
    ("color", "colour"),
    ("colors", "colours"),
    ("coloring", "colouring"),
    ("colored", "coloured"),
    ("colorless", "colourless"),
    ("neighbor", "neighbour"),
    ("neighbors", "neighbours"),
    ("neighborhood", "neighbourhood"),
    ("favor", "favour"),
    ("favors", "favours"),
    ("favored", "favoured"),
    ("favorable", "favourable"),
    ("favorably", "favourably"),
    ("unfavorable", "unfavourable"),
    ("unfavourable", "unfavorable"),
    ("favorite", "favourite"),
    ("favorites", "favourites"),
    ("flavor", "flavour"),
    ("flavors", "flavours"),
    ("flavored", "flavoured"),
    ("flavoring", "flavouring"),
    ("harbor", "harbour"),
    ("harbors", "harbours"),
    ("labor", "labour"),
    ("labors", "labours"),
    ("labored", "laboured"),
    ("laborer", "labourer"),
    ("laborers", "labourers"),
    ("laboring", "labouring"),
    ("behavior", "behaviour"),
    ("behaviors", "behaviours"),
    ("behavioral", "behavioural"),
    ("behaviour", "behaviour"),
    ("rumor", "rumour"),
    ("rumors", "rumours"),
    ("valor", "valour"),
    ("ardor", "ardour"),
    ("vigor", "vigour"),
    ("rigor", "rigour"),
    ("rigors", "rigours"),
    ("rigorous", "rigorous"),  # same
    ("rigourous", "rigourous"),  # same
    ("splendor", "splendour"),
    ("splendors", "splendours"),
    ("splendid", "splendid"),  # same
    ("humor", "humour"),
    ("humors", "humours"),
    ("humorous", "humorous"),  # same
    ("tumor", "tumour"),
    ("tumors", "tumours"),
    ("tremor", "tremour"),  # rare
    ("armor", "armour"),
    ("armors", "armours"),
    ("armored", "armoured"),
    ("ardor", "ardour"),
    ("clamor", "clamour"),
    ("clamors", "clamours"),
    ("clangor", "clangour"),
    ("candor", "candour"),
    (" endeavor", "endeavour"),
    (" endeavors", "endeavours"),
    ("demeanor", "demeanour"),
    ("savior", "saviour"),
    ("saviors", "saviours"),
    ("parlour", "parlor"),
    ("vapour", "vapor"),
    ("vapors", "vapors"),
    ("odour", "odor"),
    ("odours", "odors"),
    ("murmur", "murmur"),  # same
    (" glamour", "glamour"),  # same in both
    ("rancor", "rancour"),
    ("error", "error"),  # same
    ("terror", "terror"),  # same
    ("horror", "horror"),  # same
    ("mirror", "mirror"),  # same
    ("donor", "donor"),  # same
    (" creditors", "creditors"),  # same

    # -re / -er
    ("center", "centre"),
    ("centers", "centres"),
    ("centered", "centred"),
    ("theater", "theatre"),
    ("theaters", "theatres"),
    ("meter", "metre"),
    ("kilometer", "kilometre"),
    ("liters", "litres"),
    ("liter", "litre"),
    ("litre", "liter"),
    ("litres", "liters"),
    ("deciliter", "decilitre"),
    ("deciliters", "decilitres"),
    ("milliliter", "millilitre"),
    ("milliliters", "millilitres"),
    ("centiliter", "centilitre"),
    ("centiliters", "centilitres"),
    ("hectare", "hectare"),  # same
    ("fiber", "fibre"),
    ("fibers", "fibres"),
    ("meager", "meagre"),
    ("meagerly", "meagrely"),
    ("somber", "sombre"),
    ("luster", "lustre"),
    ("lustre", "luster"),
    ("specter", "spectre"),
    ("specters", "spectres"),
    ("philter", "philtre"),
    ("sepulcher", "sepulchre"),
    ("chapter", "chapter"),  # same
    ("father", "father"),  # same
    ("mother", "mother"),  # same
    ("brother", "brother"),  # same
    ("other", "other"),  # same
    ("monster", "monster"),  # same
    ("minster", "minster"),  # same
    ("louvre", "louvre"),  # same

    # -ise / -ize (British -ise is common; American standardizes -ize)
    ("organize", "organise"),
    ("organized", "organised"),
    ("organizing", "organising"),
    ("organization", "organisation"),
    ("organizations", "organisations"),
    ("recognize", "recognise"),
    ("recognized", "recognised"),
    ("recognizing", "recognising"),
    ("realize", "realise"),
    ("realized", "realised"),
    ("realizing", "realising"),
    ("realization", "realisation"),
    ("analyze", "analyse"),
    ("analyzed", "analysed"),
    ("analyzing", "analysing"),
    ("analysis", "analysis"),  # same
    ("apologize", "apologise"),
    ("apologized", "apologised"),
    ("apologizing", "apologising"),
    ("apologize", "apologise"),
    ("authorize", "authorise"),
    ("authorized", "authorised"),
    ("authorizing", "authorising"),
    ("authorization", "authorisation"),
    ("civilize", "civilise"),
    ("civilized", "civilised"),
    ("civilizing", "civilising"),
    ("civilization", "civilisation"),
    ("capitalize", "capitalise"),
    ("capitalized", "capitalised"),
    ("criticize", "criticise"),
    ("criticized", "criticised"),
    ("criticizing", "criticising"),
    ("criticism", "criticism"),  # same
    ("characterize", "characterise"),
    ("characterized", "characterised"),
    ("characterizing", "characterising"),
    ("commercialize", "commercialise"),
    ("commercialized", "commercialised"),
    ("centralize", "centralise"),
    ("centralized", "centralised"),
    ("centralizing", "centralising"),
    ("colonize", "colonise"),
    ("colonized", "colonised"),
    ("colonizing", "colonising"),
    ("colonization", "colonisation"),
    ("emphasize", "emphasise"),
    ("emphasized", "emphasised"),
    ("emphasizing", "emphasising"),
    ("equalize", "equalise"),
    ("equalized", "equalised"),
    ("equalizing", "equalising"),
    ("familiarize", "familiarise"),
    ("familiarized", "familiarised"),
    ("familiarizing", "familiarising"),
    ("generalize", "generalise"),
    ("generalized", "generalised"),
    ("generalizing", "generalising"),
    ("harmonize", "harmonise"),
    ("harmonized", "harmonised"),
    ("harmonizing", "harmonising"),
    ("humanize", "humanise"),
    ("humanized", "humanised"),
    ("humanizing", "humanising"),
    ("idealize", "idealise"),
    ("idealized", "idealised"),
    ("idealizing", "idealising"),
    ("individualize", "individualise"),
    ("individualized", "individualised"),
    ("individualizing", "individualising"),
    ("industrialize", "industrialise"),
    ("industrialized", "industrialised"),
    ("industrializing", "industrialising"),
    ("industrialization", "industrialisation"),
    ("legalize", "legalise"),
    ("legalized", "legalised"),
    ("legalizing", "legalising"),
    ("legitimize", "legitimise"),
    ("legitimized", "legitimised"),
    ("legitimizing", "legitimising"),
    ("materialize", "materialise"),
    ("materialized", "materialised"),
    ("materializing", "materialising"),
    ("maximize", "maximise"),
    ("maximized", "maximised"),
    ("maximizing", "maximising"),
    ("minimize", "minimise"),
    ("minimized", "minimised"),
    ("minimizing", "minimising"),
    ("modernize", "modernise"),
    ("modernized", "modernised"),
    ("modernizing", "modernising"),
    ("modernization", "modernisation"),
    ("naturalize", "naturalise"),
    ("naturalized", "naturalised"),
    ("naturalizing", "naturalising"),
    ("normalize", "normalise"),
    ("normalized", "normalised"),
    ("normalizing", "normalising"),
    ("patronize", "patronise"),
    ("patronized", "patronised"),
    ("patronizing", "patronising"),
    ("personalize", "personalise"),
    ("personalized", "personalised"),
    ("personalizing", "personalising"),
    ("rationalize", "rationalise"),
    ("rationalized", "rationalised"),
    ("rationalizing", "rationalising"),
    ("realize", "realise"),
    ("realized", "realised"),
    ("realizing", "realising"),
    ("specialize", "specialise"),
    ("specialized", "specialised"),
    ("specializing", "specialising"),
    ("specialization", "specialisation"),
    ("standardize", "standardise"),
    ("standardized", "standardised"),
    ("standardizing", "standardising"),
    ("summarize", "summarise"),
    ("summarized", "summarised"),
    ("summarizing", "summarising"),
    ("symbolize", "symbolise"),
    ("symbolized", "symbolised"),
    ("symbolizing", "symbolising"),
    ("synthesize", "synthesise"),
    ("synthesized", "synthesised"),
    ("synthesizing", "synthesising"),
    ("systematize", "systematise"),
    ("systematized", "systematised"),
    ("systematizing", "systematising"),
    ("temporize", "temporise"),
    ("temporized", "temporised"),
    ("temporizing", "temporising"),
    ("theorize", "theorise"),
    ("theorized", "theorised"),
    ("theorizing", "theorising"),
    ("victimize", "victimise"),
    ("victimized", "victimised"),
    ("victimized", "victimised"),
    ("victimize", "victimise"),
    ("visualize", "visualise"),
    ("visualized", "visualised"),
    ("visualizing", "visualising"),
    ("vocalize", "vocalise"),
    ("vocalized", "vocalised"),
    ("vocalizing", "vocalising"),
    ("socialize", "socialise"),
    ("socialized", "socialised"),
    ("socializing", "socialising"),
    ("stigmatize", "stigmatise"),
    ("stigmatized", "stigmatised"),
    ("stigmatizing", "stigmatising"),

    # -yse / -yze
    ("analyze", "analyse"),
    ("analyzed", "analysed"),
    ("analyzing", "analysing"),
    ("paralyze", "paralyse"),
    ("paralyzed", "paralysed"),
    ("paralyzing", "paralysing"),

    # -ce / -se (noun vs verb distinction sometimes differs)
    ("defense", "defence"),
    ("defenses", "defences"),
    ("defensive", "defensive"),  # same
    ("offense", "offence"),
    ("offenses", "offences"),
    ("offensive", "offensive"),  # same
    ("pretense", "pretence"),
    ("pretenses", "pretences"),
    ("license", "licence"),  # US: noun=license, verb=license; UK: noun=licence, verb=license
    ("practice", "practice"),  # same noun; US: verb=practice; UK: verb=practise
    ("practise", "practice"),
    ("prophecy", "prophecy"),  # same noun; verb=prophesy in both
    ("license", "licence"),

    # -ence / -ense (limited set)
    ("defense", "defence"),
    ("offense", "offence"),
    ("pretense", "pretence"),
    ("presence", "presence"),  # same
    ("absence", "absence"),  # same
    ("silence", "silence"),  # same
    ("violence", "violence"),  # same
    ("patience", "patience"),  # same
    ("sentence", "sentence"),  # same
    ("fence", "fence"),  # same

    # -ogue / -og
    ("analog", "analogue"),
    ("analogs", "analogues"),
    ("catalog", "catalogue"),
    ("catalogs", "catalogues"),
    ("cataloged", "catalogued"),
    ("cataloging", "cataloguing"),
    ("dialog", "dialogue"),
    ("dialogs", "dialogues"),
    ("dialogue", "dialog"),
    ("epilog", "epilogue"),
    ("epilogues", "epilogues"),
    ("monolog", "monologue"),
    ("monologue", "monolog"),
    ("monologs", "monologues"),
    ("prolog", "prologue"),
    ("prologue", "prolog"),
    ("prologs", "prologues"),
    ("travelog", "travelogue"),

    # -ll- / -l- (British double-L in some verb forms)
    ("traveled", "travelled"),
    ("traveler", "traveller"),
    ("traveling", "travelling"),
    ("travelings", "travellings"),
    ("canceled", "cancelled"),
    ("canceling", "cancelling"),
    ("cancellation", "cancellation"),  # same
    ("signaled", "signalled"),
    ("signaling", "signalling"),
    ("marveled", "marvelled"),
    ("marveling", "marvelling"),
    ("libeled", "libelled"),
    ("libeling", "libelling"),
    ("quartered", "quartered"),  # same
    ("fueling", "fuelling"),
    ("fuelled", "fueled"),
    ("fueled", "fuelled"),
    ("fuelling", "fueling"),
    ("parceling", "parcelling"),
    ("sniveled", "snivelled"),
    ("sniveling", "snivelling"),
    ("compelled", "compelled"),  # same
    ("expelled", "expelled"),  # same
    ("propelled", "propelled"),  # same
    ("excelled", "excelled"),  # same
    ("paralleled", "paralleled"),  # same
    ("quarrelled", "quarreled"),
    ("quarreling", "quarrelling"),
    ("distilled", "distilled"),  # same
    ("fulfilled", "fulfilled"),  # same
    ("instilled", "instilled"),  # same
    ("enthralled", "enthralled"),  # same
    ("installed", "installed"),  # same
    ("enrolled", "enrolled"),  # same
    ("controlled", "controlled"),  # same
    ("modelled", "modeled"),
    ("modelling", "modeling"),
    ("labelled", "labeled"),
    ("labelling", "labeling"),
    ("labelling", "labeling"),
    ("jeweled", "jewelled"),
    ("jeweling", "jewelling"),
    ("rivalled", "rivaled"),
    ("rivalling", "rivaling"),
    ("shrivelled", "shriveled"),
    ("swooned", "swooned"),  # same
    ("ballooned", "ballooned"),  # same
    ("groveled", "grovelled"),
    ("groveling", "grovelling"),
    ("kidnapped", "kidnapped"),  # same
    ("worshipped", "worshipped"),  # same
    ("handicapped", "handicapped"),  # same
    ("mapped", "mapped"),  # same
    ("tapped", "tapped"),  # same
    ("slipping", "slipping"),  # same
    ("snapping", "snapping"),  # same
    ("stopped", "stopped"),  # same
    ("stepped", "stepped"),  # same

    # -ction / -xion
    ("connection", "connexion"),
    ("connections", "connexions"),
    ("connexion", "connection"),
    ("inflexion", "inflection"),
    ("inflexions", "inflections"),
    ("reflexion", "reflection"),
    ("reflexions", "reflections"),
    ("complexion", "complexion"),  # same
    ("circumflexion", "circumflexion"),  # same

    # -gment / -gement
    ("judgment", "judgement"),
    ("acknowledgment", "acknowledgement"),
    ("acknowledgments", "acknowledgements"),
    ("abridgment", "abridgement"),
    ("abridgments", "abridgements"),
    ("adjudgment", "adjudgement"),
    ("lodgment", "lodgement"),
    ("lodgments", "lodgements"),
    ("filament", "filament"),  # same
    ("fragment", "fragment"),  # same
    ("segment", "segment"),  # same
    ("pigment", "pigment"),  # same
    ("figment", "figment"),  # same

    # -nse / -nce where British may differ
    ("defense", "defence"),
    ("offense", "offence"),
    ("pretense", "pretence"),
    ("license", "licence"),

    # -ae- / -e- (Latin-derived words)
    ("aesthetic", "aesthetic"),  # same in both
    ("aetiology", "etiology"),
    ("aetiologic", "etiologic"),
    ("aemia", "emia"),  # hemophilia etc.
    ("gynecology", "gynaecology"),
    ("gynecological", "gynaecological"),
    ("pediatric", "paediatric"),
    ("pediatrics", "paediatrics"),
    ("orthopedic", "orthopaedic"),
    ("orthopedics", "orthopaedics"),
    ("esthetics", "aesthetics"),  # reversed!
    ("archeology", "archaeology"),
    ("archeological", "archaeological"),
    ("encyclopedia", "encyclopaedia"),
    ("encyclopedic", "encyclopaedic"),
    ("maneuver", "manoeuvre"),
    ("maneuvered", "manoeuvred"),
    ("maneuvering", "manoeuvring"),
    ("maneuvers", "manoeuvres"),
    ("measles", "measles"),  # same
    ("eon", "aeon"),
    ("eonian", "aeonian"),
    ("eons", "aeons"),

    # -oe- / -e-
    ("maneuver", "manoeuvre"),
    ("maneuvered", "manoeuvred"),
    ("maneuvering", "manoeuvring"),
    ("maneuvers", "manoeuvres"),
    ("fetus", "foetus"),
    ("fetuses", "foetuses"),
    ("fetal", "foetal"),
    ("estrogen", "oestrogen"),
    ("esophagus", "oesophagus"),
    ("esophageal", "oesophageal"),
    ("edema", "oedema"),
    ("hematology", "haematology"),
    ("hemoglobin", "haemoglobin"),
    ("hemorrhage", "haemorrhage"),
    ("hemorrhage", "haemorrhage"),
    ("hemorrhoids", "haemorrhoids"),

    # -yze / -yse
    ("analyze", "analyse"),
    ("analyzed", "analysed"),
    ("analyzing", "analysing"),
    ("paralyze", "paralyse"),
    ("paralyzed", "paralysed"),
    ("paralyzing", "paralysing"),
    ("catalyze", "catalyse"),
    ("catalyzed", "catalysed"),
    ("catalyzing", "catalysing"),

    # Miscellaneous spelling differences
    ("acknowledgment", "acknowledgement"),
    ("acknowledgments", "acknowledgements"),
    ("aging", "ageing"),
    ("aggrandizement", "aggrandisement"),
    ("agonize", "agonise"),
    ("agonized", "agonised"),
    ("agonizing", "agonising"),
    ("aluminum", "aluminium"),
    ("amortize", "amortise"),
    ("amortized", "amortised"),
    ("amortizing", "amortising"),
    ("amphitheater", "amphitheatre"),
    ("amuse", "amuse"),  # same
    ("appall", "appal"),
    ("appalled", "appalled"),  # same
    ("appalling", "appalling"),  # same
    ("appetizer", "appetiser"),
    ("appetizer", "appetiser"),
    ("arbor", "arbour"),
    ("ardor", "ardour"),
    ("armoire", "armoire"),  # same
    ("artifact", "artefact"),
    ("artifacts", "artefacts"),
    ("author", "author"),  # same
    ("ax", "axe"),
    ("axe", "ax"),
    ("bacteriology", "bacteriology"),  # same
    ("balk", "baulk"),
    ("banister", "banister"),  # same
    ("barrister", "barrister"),  # same
    ("basket", "basket"),  # same
    ("bauble", "bauble"),  # same
    ("behaviour", "behavior"),
    ("belabor", "belabour"),
    ("belittled", "belittled"),  # same
    ("benediction", "benediction"),  # same
    ("besiege", "besiege"),  # same
    ("bowdlerize", "bowdlerise"),
    ("bowdlerized", "bowdlerised"),
    ("brutalize", "brutalise"),
    ("brutalized", "brutalised"),
    ("brutalizing", "brutalising"),
    ("brunette", "brunette"),  # same
    ("candy", "candy"),  # same (sweets)
    ("carburetor", "carburettor"),
    ("carburetors", "carburettors"),
    ("catalyze", "catalyse"),
    ("categorize", "categorise"),
    ("categorized", "categorised"),
    ("categorizing", "categorising"),
    ("cavil", "cavil"),  # same
    ("cellist", "cellist"),  # same
    ("chili", "chilli"),
    ("chili", "chilli"),
    ("cigarette", "cigarette"),  # same
    ("cipher", "cipher"),  # same
    ("clamor", "clamour"),
    ("cognizant", "cognisant"),
    ("cohabit", "cohabit"),  # same
    ("collaborate", "collaborate"),  # same
    ("collectible", "collectable"),  # both accepted in both
    ("collectable", "collectible"),
    ("colonize", "colonise"),
    ("coloration", "coloration"),  # same
    ("colorless", "colourless"),
    ("commercialize", "commercialise"),
    ("commemorate", "commemorate"),  # same
    ("compact", "compact"),  # same
    ("compartment", "compartment"),  # same
    ("compel", "compel"),  # same
    ("complement", "complement"),  # same
    ("computerize", "computerise"),
    ("computerized", "computerised"),
    ("computerizing", "computerising"),
    ("conceal", "conceal"),  # same
    ("conceit", "conceit"),  # same
    ("concoct", "concoct"),  # same
    ("conglomerate", "conglomerate"),  # same
    ("conqueror", "conqueror"),  # same
    ("conscience", "conscience"),  # same
    ("conscientious", "conscientious"),  # same
    ("consecrated", "consecrated"),  # same
    ("consider", "consider"),  # same
    ("consolation", "consolation"),  # same
    ("conspicuous", "conspicuous"),  # same
    ("constitute", "constitute"),  # same
    ("constrain", "constrain"),  # same
    ("construct", "construct"),  # same
    ("consumer", "consumer"),  # same
    ("contemplate", "contemplate"),  # same
    ("contempt", "contempt"),  # same
    ("content", "content"),  # same
    ("continuance", "continuance"),  # same
    ("contraction", "contraction"),  # same
    ("controversy", "controversy"),  # same
    ("convert", "convert"),  # same
    ("convulse", "convulse"),  # same
    ("coolly", "coolly"),  # same
    ("cooperation", "co-operation"),  # hyphenation
    ("coordination", "co-ordination"),  # hyphenation
    ("counselor", "counsellor"),
    ("counselors", "counsellors"),
    ("counterfeit", "counterfeit"),  # same
    ("counterpart", "counterpart"),  # same
    ("counterweight", "counterweight"),  # same
    ("courage", "courage"),  # same
    ("courteous", "courteous"),  # same
    ("courtesy", "courtesy"),  # same
    ("covetous", "covetous"),  # same
    ("creditor", "creditor"),  # same
    ("crystallize", "crystallise"),
    ("crystallized", "crystallised"),
    ("crystallizing", "crystallising"),
    ("curb", "curb"),  # same
    ("cyclopedia", "cyclopaedia"),
    ("czar", "tsar"),  # historical, not strictly US/UK
    ("dealing", "dealing"),  # same
    ("debase", "debase"),  # same
    ("debate", "debate"),  # same
    ("decal", "decal"),  # same
    ("deception", "deception"),  # same
    ("decrepit", "decrepit"),  # same
    ("decriminalize", "decriminalise"),
    ("decrypt", "decrypt"),  # same
    ("defection", "defection"),  # same
    ("defender", "defender"),  # same
    ("defense", "defence"),
    ("defensive", "defensive"),  # same
    ("defiance", "defiance"),  # same
    ("defoliant", "defoliant"),  # same
    ("deformation", "deformation"),  # same
    ("degradation", "degradation"),  # same
    ("delegate", "delegate"),  # same
    ("deliberate", "deliberate"),  # same
    ("demagnetize", "demagnetise"),
    ("demilitarize", "demilitarise"),
    ("demobilize", "demobilise"),
    ("democrat", "democrat"),  # same
    ("demographic", "demographic"),  # same
    ("demon", "demon"),  # same
    ("denigrate", "denigrate"),  # same
    ("denote", "denote"),  # same
    ("dense", "dense"),  # same
    ("depend", "depend"),  # same
    ("depict", "depict"),  # same
    ("deploy", "deploy"),  # same
    ("depletion", "depletion"),  # same
    ("deportment", "deportment"),  # same
    ("depression", "depression"),  # same
    ("deprive", "deprive"),  # same
    ("deputize", "deputise"),
    ("deputized", "deputised"),
    ("derivation", "derivation"),  # same
    ("desalinate", "desalinate"),  # same
    ("descend", "descend"),  # same
    ("desiccate", "desiccate"),  # same
    ("despatch", "dispatch"),  # British: despatch (variant), dispatch (standard)
    ("dispatch", "despatch"),
    ("desperate", "desperate"),  # same
    ("despise", "despise"),  # same
    ("destabilize", "destabilise"),
    ("detach", "detach"),  # same
    ("detail", "detail"),  # same
    ("deterrent", "deterrent"),  # same
    ("develop", "develop"),  # same
    ("deviate", "deviate"),  # same
    ("devour", "devour"),  # same
    ("dialogue", "dialog"),
    ("diamond", "diamond"),  # same
    ("diaper", "nappy"),  # different words
    ("differ", "differ"),  # same
    ("digest", "digest"),  # same
    ("dilemma", "dilemma"),  # same
    ("diminish", "diminish"),  # same
    ("dine", "dine"),  # same
    ("diplomat", "diplomat"),  # same
    ("direct", "direct"),  # same
    ("disassemble", "disassemble"),  # same
    ("disc", "disk"),  # computing vs general
    ("discolor", "discolour"),
    ("discolored", "discoloured"),
    ("disfavor", "disfavour"),
    ("disguise", "disguise"),  # same
    ("disillusion", "disillusion"),  # same
    ("disinfect", "disinfect"),  # same
    ("dislocate", "dislocate"),  # same
    ("dismantle", "dismantle"),  # same
    ("dismiss", "dismiss"),  # same
    ("disorder", "disorder"),  # same
    ("dispatch", "despatch"),
    ("dispatched", "despatched"),
    ("dispatches", "despatches"),
    ("dispense", "dispense"),  # same
    ("dispersal", "dispersal"),  # same
    ("displace", "displace"),  # same
    ("displease", "displease"),  # same
    ("disposal", "disposal"),  # same
    ("dispose", "dispose"),  # same
    ("disproportionate", "disproportionate"),  # same
    ("dispute", "dispute"),  # same
    ("dissect", "dissect"),  # same
    ("disseminate", "disseminate"),  # same
    ("dissociate", "dissociate"),  # same
    ("dissuade", "dissuade"),  # same
    ("distill", "distil"),
    ("distilled", "distilled"),  # same
    ("distinction", "distinction"),  # same
    ("distort", "distort"),  # same
    ("distract", "distract"),  # same
    ("distribute", "distribute"),  # same
    ("district", "district"),  # same
    ("disuse", "disuse"),  # same
    ("diverge", "diverge"),  # same
    ("diversify", "diversify"),  # same
    ("divert", "divert"),  # same
    ("divulge", "divulge"),  # same
    ("doctor", "doctor"),  # same
    ("document", "document"),  # same
    ("domain", "domain"),  # same
    ("dominate", "dominate"),  # same
    ("donate", "donate"),  # same
    ("dormant", "dormant"),  # same
    ("draft", "draught"),  # beer context; also draft=draught
    ("draped", "draped"),  # same
    ("dumbbell", "dumbbell"),  # same
    ("durability", "durability"),  # same
    ("dwarf", "dwarf"),  # same
    ("dwell", "dwell"),  # same
    ("dynamic", "dynamic"),  # same

    # E
    ("earmold", "earmould"),
    ("eccentric", "eccentric"),  # same
    ("eclipse", "eclipse"),  # same
    ("economize", "economise"),
    ("economized", "economised"),
    ("economizing", "economising"),
    ("economy", "economy"),  # same
    ("edema", "oedema"),
    ("editor", "editor"),  # same
    ("editorial", "editorial"),  # same
    ("effect", "effect"),  # same
    ("efficiency", "efficiency"),  # same
    ("efficient", "efficient"),  # same
    ("effigy", "effigy"),  # same
    ("effort", "effort"),  # same
    ("egoist", "egoist"),  # same
    ("elaborate", "elaborate"),  # same
    ("electrolyze", "electrolyse"),
    ("elegance", "elegance"),  # same
    ("elevate", "elevate"),  # same
    ("eliminate", "eliminate"),  # same
    ("elite", "elite"),  # same
    ("embargo", "embargo"),  # same
    ("embassy", "embassy"),  # same
    ("embody", "embody"),  # same
    ("embolden", "embolden"),  # same
    ("embrace", "embrace"),  # same
    ("embryo", "embryo"),  # same
    ("emerald", "emerald"),  # same
    ("emigrate", "emigrate"),  # same
    ("emission", "emission"),  # same
    ("emperor", "emperor"),  # same
    ("empire", "empire"),  # same
    ("empower", "empower"),  # same
    ("enamored", "enamoured"),
    ("enclose", "enclose"),  # same
    ("enclosure", "enclosure"),  # same
    ("encode", "encode"),  # same
    ("encyclopedia", "encyclopaedia"),
    ("endorse", "endorse"),  # same
    ("endurance", "endurance"),  # same
    ("enforce", "enforce"),  # same
    ("engage", "engage"),  # same
    ("engine", "engine"),  # same
    ("engineer", "engineer"),  # same
    ("engraft", "engraft"),  # same
    ("engrave", "engrave"),  # same
    ("enhance", "enhance"),  # same
    ("enlarge", "enlarge"),  # same
    ("enlighten", "enlighten"),  # same
    ("enlist", "enlist"),  # same
    ("enmity", "enmity"),  # same
    ("enormous", "enormous"),  # same
    ("enquire", "inquire"),  # UK: enquire is common
    ("enquiry", "inquiry"),
    ("enroll", "enrol"),
    ("enrolled", "enrolled"),  # same
    ("enrollment", "enrolment"),
    ("ensemble", "ensemble"),  # same
    ("ensure", "ensure"),  # same
    ("enterprise", "enterprise"),  # same
    ("entertain", "entertain"),  # same
    ("enthusiasm", "enthusiasm"),  # same
    ("entire", "entire"),  # same
    ("entitle", "entitle"),  # same
    ("entity", "entity"),  # same
    ("entrap", "entrap"),  # same
    ("envelope", "envelope"),  # same
    ("envious", "envious"),  # same
    ("epaulet", "epaulette"),
    ("epicenter", "epicentre"),
    ("epidemic", "epidemic"),  # same
    ("episode", "episode"),  # same
    ("equal", "equal"),  # same
    ("equilibrium", "equilibrium"),  # same
    ("equip", "equip"),  # same
    ("equity", "equity"),  # same
    ("equivocal", "equivocal"),  # same
    ("eradicate", "eradicate"),  # same
    ("erosion", "erosion"),  # same
    ("erotic", "erotic"),  # same
    ("err", "err"),  # same
    ("errand", "errand"),  # same
    ("escalate", "escalate"),  # same
    ("escapade", "escapade"),  # same
    ("escort", "escort"),  # same
    ("esophagus", "oesophagus"),
    ("espionage", "espionage"),  # same
    ("essay", "essay"),  # same
    ("essence", "essence"),  # same
    ("establish", "establish"),  # same
    ("estate", "estate"),  # same
    ("estimate", "estimate"),  # same
    ("ethic", "ethic"),  # same
    ("etiquette", "etiquette"),  # same
    ("evacuate", "evacuate"),  # same
    ("evaluate", "evaluate"),  # same
    ("evangelize", "evangelise"),
    ("evaporate", "evaporate"),  # same
    ("evasive", "evasive"),  # same
    ("evident", "evident"),  # same
    ("evil", "evil"),  # same
    ("evoke", "evoke"),  # same
    ("evolution", "evolution"),  # same
    ("exaggerate", "exaggerate"),  # same
    ("exalt", "exalt"),  # same
    ("examine", "examine"),  # same
    ("example", "example"),  # same
    ("exasperate", "exasperate"),  # same
    ("excavate", "excavate"),  # same
    ("exceed", "exceed"),  # same
    ("excel", "excel"),  # same
    ("excerpt", "excerpt"),  # same
    ("exchange", "exchange"),  # same
    ("excite", "excite"),  # same
    ("exclude", "exclude"),  # same
    ("excrete", "excrete"),  # same
    ("excursion", "excursion"),  # same
    ("excuse", "excuse"),  # same
    ("exercise", "exercise"),  # same
    ("exert", "exert"),  # same
    ("exhaust", "exhaust"),  # same
    ("exhibit", "exhibit"),  # same
    ("exhilarate", "exhilarate"),  # same
    ("exhort", "exhort"),  # same
    ("exile", "exile"),  # same
    ("exist", "exist"),  # same
    ("exorcize", "exorcise"),
    ("exotic", "exotic"),  # same
    ("expand", "expand"),  # same
    ("expect", "expect"),  # same
    ("expedition", "expedition"),  # same
    ("expel", "expel"),  # same
    ("expense", "expense"),  # same
    ("expire", "expire"),  # same
    ("explain", "explain"),  # same
    ("explicit", "explicit"),  # same
    ("explode", "explode"),  # same
    ("exploit", "exploit"),  # same
    ("exploration", "exploration"),  # same
    ("explore", "explore"),  # same
    ("explosion", "explosion"),  # same
    ("export", "export"),  # same
    ("expose", "expose"),  # same
    ("exposition", "exposition"),  # same
    ("express", "express"),  # same
    ("expulsion", "expulsion"),  # same
    ("extemporize", "extemporise"),
    ("extol", "extol"),  # same
    ("extract", "extract"),  # same
    ("extradite", "extradite"),  # same
    ("extravagant", "extravagant"),  # same
    ("extreme", "extreme"),  # same
    ("exuberant", "exuberant"),  # same
    ("eyebrow", "eyebrow"),  # same
    ("eyelash", "eyelash"),  # same

    # F
    ("fabric", "fabric"),  # same
    ("fabricate", "fabricate"),  # same
    ("face", "face"),  # same
    ("faction", "faction"),  # same
    ("faculty", "faculty"),  # same
    ("fagot", "faggot"),  # historical
    ("failure", "failure"),  # same
    ("fairy", "fairy"),  # same
    ("faithful", "faithful"),  # same
    ("fallacy", "fallacy"),  # same
    ("falsify", "falsify"),  # same
    ("familiar", "familiar"),  # same
    ("famine", "famine"),  # same
    ("fanatic", "fanatic"),  # same
    ("fanfare", "fanfare"),  # same
    ("fantasy", "fantasy"),  # same
    ("fascinate", "fascinate"),  # same
    ("fashion", "fashion"),  # same
    ("fasten", "fasten"),  # same
    ("fatigue", "fatigue"),  # same
    ("favor", "favour"),
    ("fawn", "fawn"),  # same
    ("fear", "fear"),  # same
    ("feasible", "feasible"),  # same
    ("feast", "feast"),  # same
    ("feat", "feat"),  # same
    ("feature", "feature"),  # same
    ("federate", "federate"),  # same
    ("feminine", "feminine"),  # same
    ("feminize", "feminise"),
    ("fence", "fence"),  # same
    ("fend", "fend"),  # same
    ("ferment", "ferment"),  # same
    ("ferocious", "ferocious"),  # same
    ("fertile", "fertile"),  # same
    ("fervent", "fervent"),  # same
    ("fervor", "fervour"),
    ("festival", "festival"),  # same
    ("fetid", "fetid"),  # same
    ("fetus", "foetus"),
    ("feud", "feud"),  # same
    ("fever", "fever"),  # same
    ("fiance", "fiancé"),  # accent only
    ("fidelity", "fidelity"),  # same
    ("fiend", "fiend"),  # same
    ("figment", "figment"),  # same
    ("figure", "figure"),  # same
    ("filament", "filament"),  # same
    ("fillet", "fillet"),  # same
    ("filter", "filter"),  # same
    ("final", "final"),  # same
    ("finale", "finale"),  # same
    ("finance", "finance"),  # same
    ("financial", "financial"),  # same
    ("fingerprint", "fingerprint"),  # same
    ("finish", "finish"),  # same
    ("fissure", "fissure"),  # same
    ("fist", "fist"),  # same
    ("flame", "flame"),  # same
    ("flannel", "flannel"),  # same
    ("flare", "flare"),  # same
    ("flash", "flash"),  # same
    ("flatter", "flatter"),  # same
    ("flavor", "flavour"),
    ("flaw", "flaw"),  # same
    ("flee", "flee"),  # same
    ("flesh", "flesh"),  # same
    ("flexible", "flexible"),  # same
    ("flight", "flight"),  # same
    ("flimsy", "flimsy"),  # same
    ("float", "float"),  # same
    ("flock", "flock"),  # same
    ("flood", "flood"),  # same
    ("flora", "flora"),  # same
    ("flourish", "flourish"),  # same
    ("fluent", "fluent"),  # same
    ("fluid", "fluid"),  # same
    ("flush", "flush"),  # same
    ("flute", "flute"),  # same
    ("focus", "focus"),  # same
    ("fog", "fog"),  # same
    ("foil", "foil"),  # same
    ("fold", "fold"),  # same
    ("folk", "folk"),  # same
    ("follow", "follow"),  # same
    ("folly", "folly"),  # same
    ("fond", "fond"),  # same
    ("font", "font"),  # same
    ("fool", "fool"),  # same
    ("footwear", "footwear"),  # same
    ("forbear", "forbear"),  # same
    ("force", "force"),  # same
    ("ford", "ford"),  # same
    ("forecast", "forecast"),  # same
    ("forestall", "forestall"),  # same
    ("forgather", "forgather"),  # same
    ("forge", "forge"),  # same
    ("forgive", "forgive"),  # same
    ("fork", "fork"),  # same
    ("formal", "formal"),  # same
    ("format", "format"),  # same
    ("formidable", "formidable"),  # same
    ("formula", "formula"),  # same
    ("fortify", "fortify"),  # same
    ("fortitude", "fortitude"),  # same
    ("fortune", "fortune"),  # same
    ("fortress", "fortress"),  # same
    ("forum", "forum"),  # same
    ("fossil", "fossil"),  # same
    ("foster", "foster"),  # same
    ("fraction", "fraction"),  # same
    ("fracture", "fracture"),  # same
    ("fragile", "fragile"),  # same
    ("fragment", "fragment"),  # same
    ("frame", "frame"),  # same
    ("franchise", "franchise"),  # same
    ("frank", "frank"),  # same
    ("frantic", "frantic"),  # same
    ("fraud", "fraud"),  # same
    ("freight", "freight"),  # same
    ("frenzy", "frenzy"),  # same
    ("fresh", "fresh"),  # same
    ("friend", "friend"),  # same
    ("fright", "fright"),  # same
    ("fringe", "fringe"),  # same
    ("frontier", "frontier"),  # same
    ("frost", "frost"),  # same
    ("frown", "frown"),  # same
    ("fruit", "fruit"),  # same
    ("fulfill", "fulfil"),
    ("fulfilled", "fulfilled"),  # same
    ("fumble", "fumble"),  # same
    ("fume", "fume"),  # same
    ("funeral", "funeral"),  # same
    ("furor", "furore"),
    ("furze", "furze"),  # same
    ("fuse", "fuse"),  # same
    ("fuss", "fuss"),  # same
    ("futile", "futile"),  # same
    ("future", "future"),  # same

    # G
    ("gage", "gauge"),  # US variant
    ("gaiety", "gaiety"),  # same
    ("gallant", "gallant"),  # same
    ("gallop", "gallop"),  # same
    ("gamble", "gamble"),  # same
    ("gamut", "gamut"),  # same
    ("gape", "gape"),  # same
    ("garbage", "garbage"),  # same
    ("garden", "garden"),  # same
    ("garlic", "garlic"),  # same
    ("garment", "garment"),  # same
    ("garrison", "garrison"),  # same
    ("gasoline", "petrol"),  # different words
    ("gasp", "gasp"),  # same
    ("gate", "gate"),  # same
    ("gather", "gather"),  # same
    ("gauge", "gauge"),  # same
    ("gauntlet", "gauntlet"),  # same
    ("gaze", "gaze"),  # same
    ("general", "general"),  # same
    ("generate", "generate"),  # same
    ("generosity", "generosity"),  # same
    ("genial", "genial"),  # same
    ("genius", "genius"),  # same
    ("gentle", "gentle"),  # same
    ("gentry", "gentry"),  # same
    ("genuine", "genuine"),  # same
    ("geography", "geography"),  # same
    ("geriatric", "geriatric"),  # same
    ("gesture", "gesture"),  # same
    ("ghetto", "ghetto"),  # same
    ("ghost", "ghost"),  # same
    ("giant", "giant"),  # same
    ("gibe", "gibe"),  # same
    ("gift", "gift"),  # same
    ("gild", "gild"),  # same
    ("gimmick", "gimmick"),  # same
    ("glacier", "glacier"),  # same
    ("glamour", "glamour"),  # same
    ("glance", "glance"),  # same
    ("glare", "glare"),  # same
    ("glass", "glass"),  # same
    ("gleam", "gleam"),  # same
    ("glide", "glide"),  # same
    ("glimpse", "glimpse"),  # same
    ("globe", "globe"),  # same
    ("gloom", "gloom"),  # same
    ("glossary", "glossary"),  # same
    ("glove", "glove"),  # same
    ("gnaw", "gnaw"),  # same
    ("goblet", "goblet"),  # same
    ("god", "god"),  # same
    ("godsend", "godsend"),  # same
    ("gonorrhea", "gonorrhoea"),
    ("gorge", "gorge"),  # same
    ("gospel", "gospel"),  # same
    ("gossip", "gossip"),  # same
    ("govern", "govern"),  # same
    ("gown", "gown"),  # same
    ("grace", "grace"),  # same
    ("gradual", "gradual"),  # same
    ("graft", "graft"),  # same
    ("grain", "grain"),  # same
    ("grandfather", "grandfather"),  # same
    ("grandmother", "grandmother"),  # same
    ("grant", "grant"),  # same
    ("grape", "grape"),  # same
    ("graph", "graph"),  # same
    ("grapple", "grapple"),  # same
    ("grate", "grate"),  # same
    ("grateful", "grateful"),  # same
    ("gratitude", "gratitude"),  # same
    ("grave", "grave"),  # same
    ("gravel", "gravel"),  # same
    ("gravity", "gravity"),  # same
    ("graze", "graze"),  # same
    ("grease", "grease"),  # same
    ("great", "great"),  # same
    ("greenhouse", "greenhouse"),  # same
    ("greet", "greet"),  # same
    ("grief", "grief"),  # same
    ("grill", "grill"),  # same
    ("grin", "grin"),  # same
    ("grip", "grip"),  # same
    ("groan", "groan"),  # same
    ("groat", "groat"),  # same
    ("grocer", "grocer"),  # same
    ("grocery", "grocery"),  # same
    ("grope", "grope"),  # same
    ("gross", "gross"),  # same
    ("ground", "ground"),  # same
    ("grouse", "grouse"),  # same
    ("grovel", "grovel"),  # same
    ("grow", "grow"),  # same
    ("growth", "growth"),  # same
    ("grudge", "grudge"),  # same
    ("gruff", "gruff"),  # same
    ("guarantee", "guarantee"),  # same
    ("guard", "guard"),  # same
    ("guerrilla", "guerrilla"),  # same
    ("guess", "guess"),  # same
    ("guest", "guest"),  # same
    ("guidance", "guidance"),  # same
    ("guide", "guide"),  # same
    ("guild", "guild"),  # same
    ("guilt", "guilt"),  # same
    ("guise", "guise"),  # same
    ("gullible", "gullible"),  # same
    ("gush", "gush"),  # same
    ("gust", "gust"),  # same
    ("gutter", "gutter"),  # same
    ("gynecology", "gynaecology"),

    # H
    ("habit", "habit"),  # same
    ("habitat", "habitat"),  # same
    ("hack", "hack"),  # same
    ("hail", "hail"),  # same
    ("hair", "hair"),  # same
    ("half", "half"),  # same
    ("hall", "hall"),  # same
    ("hallow", "hallow"),  # same
    ("halt", "halt"),  # same
    ("halve", "halve"),  # same
    ("hammer", "hammer"),  # same
    ("hamper", "hamper"),  # same
    ("hand", "hand"),  # same
    ("handbook", "handbook"),  # same
    ("handicap", "handicap"),  # same
    ("handle", "handle"),  # same
    ("handsome", "handsome"),  # same
    ("handy", "handy"),  # same
    ("hang", "hang"),  # same
    ("harass", "harass"),  # same
    ("harbor", "harbour"),
    ("hard", "hard"),  # same
    ("hardship", "hardship"),  # same
    ("hardware", "hardware"),  # same
    ("harmony", "harmony"),  # same
    ("harness", "harness"),  # same
    ("harp", "harp"),  # same
    ("harsh", "harsh"),  # same
    ("harvest", "harvest"),  # same
    ("hash", "hash"),  # same
    ("haste", "haste"),  # same
    ("hasty", "hasty"),  # same
    ("hat", "hat"),  # same
    ("hatch", "hatch"),  # same
    ("haunt", "haunt"),  # same
    ("haven", "haven"),  # same
    ("havoc", "havoc"),  # same
    ("hazard", "hazard"),  # same
    ("head", "head"),  # same
    ("heal", "heal"),  # same
    ("health", "health"),  # same
    ("heap", "heap"),  # same
    ("hear", "hear"),  # same
    ("hearth", "hearth"),  # same
    ("heart", "heart"),  # same
    ("heat", "heat"),  # same
    ("heater", "heater"),  # same
    ("heave", "heave"),  # same
    ("heavy", "heavy"),  # same
    ("hedge", "hedge"),  # same
    ("heel", "heel"),  # same
    ("hegemony", "hegemony"),  # same
    ("heir", "heir"),  # same
    ("help", "help"),  # same
    ("hematite", "haematite"),
    ("hemisphere", "hemisphere"),  # same
    ("hemlock", "hemlock"),  # same
    ("hemorrhage", "haemorrhage"),
    ("henchman", "henchman"),  # same
    ("hence", "hence"),  # same
    ("herb", "herb"),  # same (pronunciation differs)
    ("herd", "herd"),  # same
    ("heritage", "heritage"),  # same
    ("hermit", "hermit"),  # same
    ("hero", "hero"),  # same
    ("heroic", "heroic"),  # same
    ("hesitate", "hesitate"),  # same
    ("hide", "hide"),  # same
    ("hierarchy", "hierarchy"),  # same
    ("high", "high"),  # same
    ("highlight", "highlight"),  # same
    ("hike", "hike"),  # same
    ("hill", "hill"),  # same
    ("hinder", "hinder"),  # same
    ("hinge", "hinge"),  # same
    ("hint", "hint"),  # same
    ("hip", "hip"),  # same
    ("hire", "hire"),  # same
    ("historian", "historian"),  # same
    ("historic", "historic"),  # same
    ("history", "history"),  # same
    ("hit", "hit"),  # same
    ("hitch", "hitch"),  # same
    ("hoard", "hoard"),  # same
    ("hoarse", "hoarse"),  # same
    ("hobby", "hobby"),  # same
    ("hoist", "hoist"),  # same
    ("hold", "hold"),  # same
    ("hole", "hole"),  # same
    ("holiday", "holiday"),  # same
    ("hollow", "hollow"),  # same
    ("holy", "holy"),  # same
    ("homer", "homer"),  # same
    ("homestead", "homestead"),  # same
    ("honest", "honest"),  # same
    ("honor", "honour"),
    ("hook", "hook"),  # same
    ("hope", "hope"),  # same
    ("horde", "horde"),  # same
    ("horizon", "horizon"),  # same
    ("horrify", "horrify"),  # same
    ("horse", "horse"),  # same
    ("hospital", "hospital"),  # same
    ("hospitality", "hospitality"),  # same
    ("hostage", "hostage"),  # same
    ("hostile", "hostile"),  # same
    ("hostility", "hostility"),  # same
    ("hound", "hound"),  # same
    ("hour", "hour"),  # same
    ("house", "house"),  # same
    ("hover", "hover"),  # same
    ("howl", "howl"),  # same
    ("hub", "hub"),  # same
    ("hug", "hug"),  # same
    ("humble", "humble"),  # same
    ("humid", "humid"),  # same
    ("humiliate", "humiliate"),  # same
    ("humility", "humility"),  # same
    ("humor", "humour"),
    ("hundred", "hundred"),  # same
    ("hunger", "hunger"),  # same
    ("hunt", "hunt"),  # same
    ("hurdle", "hurdle"),  # same
    ("hurry", "hurry"),  # same
    ("hurt", "hurt"),  # same
    ("husband", "husband"),  # same
    ("hush", "hush"),  # same
    ("hybrid", "hybrid"),  # same
    ("hyena", "hyaena"),
    ("hymn", "hymn"),  # same
    ("hyperbole", "hyperbole"),  # same
    ("hypocrisy", "hypocrisy"),  # same
    ("hypocrite", "hypocrite"),  # same
    ("hypothesis", "hypothesis"),  # same
    ("hysteria", "hysteria"),  # same
    ("hysterical", "hysterical"),  # same

    # I
    ("iceberg", "iceberg"),  # same
    ("idea", "idea"),  # same
    ("ideal", "ideal"),  # same
    ("idealize", "idealise"),
    ("identify", "identify"),  # same
    ("identity", "identity"),  # same
    ("ideology", "ideology"),  # same
    ("idiom", "idiom"),  # same
    ("idle", "idle"),  # same
    ("ignorance", "ignorance"),  # same
    ("ignore", "ignore"),  # same
    ("illegal", "illegal"),  # same
    ("illiterate", "illiterate"),  # same
    ("illuminate", "illuminate"),  # same
    ("illusion", "illusion"),  # same
    ("illustrate", "illustrate"),  # same
    ("image", "image"),  # same
    ("imagination", "imagination"),  # same
    ("imagine", "imagine"),  # same
    ("imitate", "imitate"),  # same
    ("immature", "immature"),  # same
    ("immediacy", "immediacy"),  # same
    ("immediate", "immediate"),  # same
    ("immense", "immense"),  # same
    ("immigrant", "immigrant"),  # same
    ("imminent", "imminent"),  # same
    ("immobilize", "immobilise"),
    ("immune", "immune"),  # same
    ("impact", "impact"),  # same
    ("impair", "impair"),  # same
    ("impart", "impart"),  # same
    ("impatient", "impatient"),  # same
    ("impeach", "impeach"),  # same
    ("impediment", "impediment"),  # same
    ("impel", "impel"),  # same
    ("impenetrable", "impenetrable"),  # same
    ("imperial", "imperial"),  # same
    ("impersonate", "impersonate"),  # same
    ("impetuous", "impetuous"),  # same
    ("implacable", "implacable"),  # same
    ("implicit", "implicit"),  # same
    ("implore", "implore"),  # same
    ("impolite", "impolite"),  # same
    ("import", "import"),  # same
    ("important", "important"),  # same
    ("impose", "impose"),  # same
    ("imposing", "imposing"),  # same
    ("impotent", "impotent"),  # same
    ("impoverish", "impoverish"),  # same
    ("imprison", "imprison"),  # same
    ("impromptu", "impromptu"),  # same
    ("improve", "improve"),  # same
    ("impulse", "impulse"),  # same
    ("impunity", "impunity"),  # same
    ("impure", "impure"),  # same
    ("inaccuracy", "inaccuracy"),  # same
    ("inactivate", "inactivate"),  # same
    ("inadequate", "inadequate"),  # same
    ("inaugurate", "inaugurate"),  # same
    ("incandesce", "incandesce"),  # same
    ("incapable", "incapable"),  # same
    ("incarcerate", "incarcerate"),  # same
    ("incense", "incense"),  # same
    ("incentive", "incentive"),  # same
    ("inception", "inception"),  # same
    ("inch", "inch"),  # same
    ("incidence", "incidence"),  # same
    ("incident", "incident"),  # same
    ("incite", "incite"),  # same
    ("inclination", "inclination"),  # same
    ("include", "include"),  # same
    ("income", "income"),  # same
    ("incompetent", "incompetent"),  # same
    ("incorporate", "incorporate"),  # same
    ("increase", "increase"),  # same
    ("incredible", "incredible"),  # same
    ("inculcate", "inculcate"),  # same
    ("indebted", "indebted"),  # same
    ("indeed", "indeed"),  # same
    ("independence", "independence"),  # same
    ("indicate", "indicate"),  # same
    ("indifferent", "indifferent"),  # same
    ("indigenous", "indigenous"),  # same
    ("indigestion", "indigestion"),  # same
    ("indignant", "indignant"),  # same
    ("indirect", "indirect"),  # same
    ("indiscriminate", "indiscriminate"),  # same
    ("indispensable", "indispensable"),  # same
    ("individual", "individual"),  # same
    ("individualize", "individualise"),
    ("indoctrinate", "indoctrinate"),  # same
    ("induce", "induce"),  # same
    ("indulge", "indulge"),  # same
    ("industrial", "industrial"),  # same
    ("industrialize", "industrialise"),
    ("ineffable", "ineffable"),  # same
    ("inequality", "inequality"),  # same
    ("inequity", "inequity"),  # same
    ("inevitable", "inevitable"),  # same
    ("inexact", "inexact"),  # same
    ("inexpensive", "inexpensive"),  # same
    ("infamous", "infamous"),  # same
    ("infancy", "infancy"),  # same
    ("infant", "infant"),  # same
    ("infect", "infect"),  # same
    ("infer", "infer"),  # same
    ("inferior", "inferior"),  # same
    ("infiltrate", "infiltrate"),  # same
    ("infinite", "infinite"),  # same
    ("infirm", "infirm"),  # same
    ("inflame", "inflame"),  # same
    ("inflate", "inflate"),  # same
    ("inflict", "inflict"),  # same
    ("influence", "influence"),  # same
    ("inform", "inform"),  # same
    ("infrastructure", "infrastructure"),  # same
    ("infringe", "infringe"),  # same
    ("infuriate", "infuriate"),  # same
    ("infuse", "infuse"),  # same
    ("ingenious", "ingenious"),  # same
    ("ingenuity", "ingenuity"),  # same
    ("ingest", "ingest"),  # same
    ("ingredient", "ingredient"),  # same
    ("inhabit", "inhabit"),  # same
    ("inherent", "inherent"),  # same
    ("inherit", "inherit"),  # same
    ("inhibit", "inhibit"),  # same
    ("initial", "initial"),  # same
    ("initiate", "initiate"),  # same
    ("inject", "inject"),  # same
    ("injure", "injure"),  # same
    ("injustice", "injustice"),  # same
    ("ink", "ink"),  # same
    ("inland", "inland"),  # same
    ("inmate", "inmate"),  # same
    ("inn", "inn"),  # same
    ("inner", "inner"),  # same
    ("innocent", "innocent"),  # same
    ("innovate", "innovate"),  # same
    ("innumerable", "innumerable"),  # same
    ("input", "input"),  # same
    ("inquest", "inquest"),  # same
    ("inquire", "inquire"),  # same (UK: enquire)
    ("inquiry", "inquiry"),  # same (UK: enquiry)
    ("insane", "insane"),  # same
    ("inscription", "inscription"),  # same
    ("insect", "insect"),  # same
    ("insert", "insert"),  # same
    ("inside", "inside"),  # same
    ("insider", "insider"),  # same
    ("insight", "insight"),  # same
    ("insignificant", "insignificant"),  # same
    ("insist", "insist"),  # same
    ("insolence", "insolence"),  # same
    ("inspect", "inspect"),  # same
    ("inspiration", "inspiration"),  # same
    ("inspire", "inspire"),  # same
    ("install", "install"),  # same
    ("instance", "instance"),  # same
    ("instant", "instant"),  # same
    ("instinct", "instinct"),  # same
    ("institute", "institute"),  # same
    ("institution", "institution"),  # same
    ("instruct", "instruct"),  # same
    ("instrument", "instrument"),  # same
    ("insubordinate", "insubordinate"),  # same
    ("insult", "insult"),  # same
    ("insurance", "insurance"),  # same
    ("insure", "insure"),  # same
    ("insurgent", "insurgent"),  # same
    ("intact", "intact"),  # same
    ("intake", "intake"),  # same
    ("integrate", "integrate"),  # same
    ("integrity", "integrity"),  # same
    ("intellect", "intellect"),  # same
    ("intelligence", "intelligence"),  # same
    ("intelligent", "intelligent"),  # same
    ("intend", "intend"),  # same
    ("intense", "intense"),  # same
    ("intensity", "intensity"),  # same
    ("intent", "intent"),  # same
    ("intention", "intention"),  # same
    ("interact", "interact"),  # same
    ("intercourse", "intercourse"),  # same
    ("interest", "interest"),  # same
    ("interfere", "interfere"),  # same
    ("interior", "interior"),  # same
    ("intermediate", "intermediate"),  # same
    ("internal", "internal"),  # same
    ("international", "international"),  # same
    ("interpret", "interpret"),  # same
    ("interrogate", "interrogate"),  # same
    ("interrupt", "interrupt"),  # same
    ("intersect", "intersect"),  # same
    ("interval", "interval"),  # same
    ("intervene", "intervene"),  # same
    ("intervention", "intervention"),  # same
    ("intimate", "intimate"),  # same
    ("intimidate", "intimidate"),  # same
    ("intricate", "intricate"),  # same
    ("intrigue", "intrigue"),  # same
    ("intrinsic", "intrinsic"),  # same
    ("introduce", "introduce"),  # same
    ("intrude", "intrude"),  # same
    ("intuition", "intuition"),  # same
    ("invade", "invade"),  # same
    ("invalid", "invalid"),  # same
    ("invaluable", "invaluable"),  # same
    ("invasion", "invasion"),  # same
    ("invent", "invent"),  # same
    ("inventory", "inventory"),  # same
    ("invert", "invert"),  # same
    ("invest", "invest"),  # same
    ("investigate", "investigate"),  # same
    ("investment", "investment"),  # same
    ("invisible", "invisible"),  # same
    ("invoke", "invoke"),  # same
    ("involve", "involve"),  # same
    ("inward", "inward"),  # same
    ("iron", "iron"),  # same
    ("ironic", "ironic"),  # same
    ("irony", "irony"),  # same
    ("irregular", "irregular"),  # same
    ("irrelevant", "irrelevant"),  # same
    ("irrigate", "irrigate"),  # same
    ("irritable", "irritable"),  # same
    ("isolate", "isolate"),  # same
    ("isolation", "isolation"),  # same
    ("issue", "issue"),  # same
    ("italic", "italic"),  # same
    ("item", "item"),  # same
    ("itinerary", "itinerary"),  # same
    ("ivory", "ivory"),  # same

    # More common pairs found in old texts
    ("negotiation", "negociation"),  # archaic French-influenced spelling
    ("appreciate", "appreciate"),  # same
    ("connexion", "connection"),
    ("inflection", "inflexion"),
    ("reflection", "reflexion"),
    ("gray", "grey"),
    ("grayed", "greyed"),
    ("graying", "greying"),
    ("grays", "greys"),
    ("grayish", "greyish"),
    ("license", "licence"),
    ("licensing", "licencing"),
    ("practicable", "practicable"),  # same
    ("practically", "practically"),  # same
    ("skeptical", "sceptical"),
    ("skeptic", "sceptic"),
    ("skepticism", "scepticism"),
    ("suspect", "suspect"),  # same
    ("suspicion", "suspicion"),  # same
    ("suspicious", "suspicious"),  # same
    ("sympathize", "sympathise"),
    ("sympathized", "sympathised"),
    ("sympathizing", "sympathising"),
    ("synthesize", "synthesise"),
    ("synthesized", "synthesised"),
    ("synthesizing", "synthesising"),
    ("tranquility", "tranquillity"),
    ("willful", "wilful"),
    ("wilfully", "willfully"),
    ("fulfill", "fulfil"),
    ("fulfillment", "fulfilment"),
    ("enrollment", "enrolment"),
    ("enroll", "enrol"),
    ("entrust", "intrust"),  # archaic
    ("instalment", "installment"),
    ("installment", "instalment"),
    ("mold", "mould"),
    ("molded", "moulded"),
    ("molding", "moulding"),
    ("molder", "moulder"),
    ("moldering", "mouldering"),
    ("molds", "moulds"),
    ("smolder", "smoulder"),
    ("smoldered", "smouldered"),
    ("smoldering", "smouldering"),
    ("smolders", "smoulders"),
    ("mustache", "moustache"),
    ("pajama", "pyjama"),
    ("pajamas", "pyjamas"),
    ("plow", "plough"),
    ("plowed", "ploughed"),
    ("plowing", "ploughing"),
    ("plows", "ploughs"),
    ("pretense", "pretence"),
    ("pretenses", "pretences"),
    ("story", "storey"),  # floor of building
    ("stories", "storeys"),
    ("program", "programme"),  # computing vs general
    ("programs", "programmes"),
    ("ton", "tonne"),  # weight
    ("tons", "tonnes"),
    ("specialty", "speciality"),
    ("medieval", "mediaeval"),  # archaic British
    ("antagonize", "antagonise"),
    ("antagonized", "antagonised"),
    ("antagonizing", "antagonising"),
    ("burglarize", "burglarise"),
    ("burglarized", "burglarised"),
    ("burglarizing", "burglarising"),
    ("capitalize", "capitalise"),
    ("capitalized", "capitalised"),
    ("capitalizing", "capitalising"),
    ("characterize", "characterise"),
    ("characterized", "characterised"),
    ("characterizing", "characterising"),
    ("civilize", "civilise"),
    ("civilized", "civilised"),
    ("civilizing", "civilising"),
    ("colonize", "colonise"),
    ("colonized", "colonised"),
    ("colonizing", "colonising"),
    ("emphasize", "emphasise"),
    ("emphasized", "emphasised"),
    ("emphasizing", "emphasising"),
    ("equalize", "equalise"),
    ("equalized", "equalised"),
    ("equalizing", "equalising"),
    ("familiarize", "familiarise"),
    ("familiarized", "familiarised"),
    ("familiarizing", "familiarising"),
    ("generalize", "generalise"),
    ("generalized", "generalised"),
    ("generalizing", "generalising"),
    ("hospitalize", "hospitalise"),
    ("hospitalized", "hospitalised"),
    ("hospitalizing", "hospitalising"),
    ("hypnotize", "hypnotise"),
    ("hypnotized", "hypnotised"),
    ("hypnotizing", "hypnotising"),
    ("immobilize", "immobilise"),
    ("immobilized", "immobilised"),
    ("immobilizing", "immobilising"),
    ("immortalize", "immortalise"),
    ("immortalized", "immortalised"),
    ("immortalizing", "immortalising"),
    ("immunize", "immunise"),
    ("immunized", "immunised"),
    ("immunizing", "immunising"),
    ("imperialize", "imperialise"),
    ("imperialized", "imperialised"),
    ("impersonalize", "impersonalise"),
    ("impersonalized", "impersonalised"),
    ("impersonalizing", "impersonalising"),
    ("individualize", "individualise"),
    ("individualized", "individualised"),
    ("individualizing", "individualising"),
    ("industrialize", "industrialise"),
    ("industrialized", "industrialised"),
    ("industrializing", "industrialising"),
    ("incentivize", "incentivise"),
    ("incentivized", "incentivised"),
    ("incentivizing", "incentivising"),
    ("jeopardize", "jeopardise"),
    ("jeopardized", "jeopardised"),
    ("jeopardizing", "jeopardising"),
    ("legalize", "legalise"),
    ("legalized", "legalised"),
    ("legalizing", "legalising"),
    ("legitimize", "legitimise"),
    ("legitimized", "legitimised"),
    ("legitimizing", "legitimising"),
    ("marginalize", "marginalise"),
    ("marginalized", "marginalised"),
    ("marginalizing", "marginalising"),
    ("materialize", "materialise"),
    ("materialized", "materialised"),
    ("materializing", "materialising"),
    ("maximize", "maximise"),
    ("maximized", "maximised"),
    ("maximizing", "maximising"),
    ("mechanize", "mechanise"),
    ("mechanized", "mechanised"),
    ("mechanizing", "mechanising"),
    ("memorialize", "memorialise"),
    ("memorialized", "memorialised"),
    ("memorializing", "memorialising"),
    ("minimize", "minimise"),
    ("minimized", "minimised"),
    ("minimizing", "minimising"),
    ("mobilize", "mobilise"),
    ("mobilized", "mobilised"),
    ("mobilizing", "mobilising"),
    ("modernize", "modernise"),
    ("modernized", "modernised"),
    ("modernizing", "modernising"),
    ("naturalize", "naturalise"),
    ("naturalized", "naturalised"),
    ("naturalizing", "naturalising"),
    ("normalize", "normalise"),
    ("normalized", "normalised"),
    ("normalizing", "normalising"),
    ("nationalize", "nationalise"),
    ("nationalized", "nationalised"),
    ("nationalizing", "nationalising"),
    ("neat", "neat"),  # same
    ("neighbor", "neighbour"),
    ("neutralize", "neutralise"),
    ("neutralized", "neutralised"),
    ("neutralizing", "neutralising"),
    ("optimal", "optimal"),  # same
    ("optimize", "optimise"),
    ("optimized", "optimised"),
    ("optimizing", "optimising"),
    ("oxidize", "oxidise"),
    ("oxidized", "oxidised"),
    ("oxidizing", "oxidising"),
    ("paralyze", "paralyse"),
    ("paralyzed", "paralysed"),
    ("paralyzing", "paralysing"),
    ("patronize", "patronise"),
    ("patronized", "patronised"),
    ("patronizing", "patronising"),
    ("personalize", "personalise"),
    ("personalized", "personalised"),
    ("personalizing", "personalising"),
    ("popularize", "popularise"),
    ("popularized", "popularised"),
    ("popularizing", "popularising"),
    ("pressurize", "pressurise"),
    ("pressurized", "pressurised"),
    ("pressurizing", "pressurising"),
    ("privatize", "privatise"),
    ("privatized", "privatised"),
    ("privatizing", "privatising"),
    ("professionalize", "professionalise"),
    ("professionalized", "professionalised"),
    ("professionalizing", "professionalising"),
    ("randomize", "randomise"),
    ("randomized", "randomised"),
    ("randomizing", "randomising"),
    ("rationalize", "rationalise"),
    ("rationalized", "rationalised"),
    ("rationalizing", "rationalising"),
    ("regularize", "regularise"),
    ("regularized", "regularised"),
    ("regularizing", "regularising"),
    ("revolutionize", "revolutionise"),
    ("revolutionized", "revolutionised"),
    ("revolutionizing", "revolutionising"),
    ("satirize", "satirise"),
    ("satirized", "satirised"),
    ("satirizing", "satirising"),
    ("sensitize", "sensitise"),
    ("sensitized", "sensitised"),
    ("sensitizing", "sensitising"),
    ("scrutinize", "scrutinise"),
    ("scrutinized", "scrutinised"),
    ("scrutinizing", "scrutinising"),
    ("socialize", "socialise"),
    ("socialized", "socialised"),
    ("socializing", "socialising"),
    ("specialize", "specialise"),
    ("specialized", "specialised"),
    ("specializing", "specialising"),
    ("standardize", "standardise"),
    ("standardized", "standardised"),
    ("standardizing", "standardising"),
    ("sterilize", "sterilise"),
    ("sterilized", "sterilised"),
    ("sterilizing", "sterilising"),
    ("summarize", "summarise"),
    ("summarized", "summarised"),
    ("summarizing", "summarising"),
    ("symbolize", "symbolise"),
    ("symbolized", "symbolised"),
    ("symbolizing", "symbolising"),
    ("sympathize", "sympathise"),
    ("sympathized", "sympathised"),
    ("sympathizing", "sympathising"),
    ("synthesize", "synthesise"),
    ("synthesized", "synthesised"),
    ("synthesizing", "synthesising"),
    ("systematize", "systematise"),
    ("systematized", "systematised"),
    ("systematizing", "systematising"),
    ("theorize", "theorise"),
    ("theorized", "theorised"),
    ("theorizing", "theorising"),
    ("tolerant", "tolerant"),  # same
    ("universalize", "universalise"),
    ("universalized", "universalised"),
    ("universalizing", "universalising"),
    ("utilitarianize", "utilitarianise"),
    ("utilize", "utilise"),
    ("utilized", "utilised"),
    ("utilizing", "utilising"),
    ("vaporize", "vaporise"),
    ("vaporized", "vaporised"),
    ("vaporizing", "vaporising"),
    ("victimize", "victimise"),
    ("victimized", "victimised"),
    ("victimized", "victimised"),
    ("victimize", "victimise"),
    ("visualize", "visualise"),
    ("visualized", "visualised"),
    ("visualizing", "visualising"),
    ("vocalize", "vocalise"),
    ("vocalized", "vocalised"),
    ("vocalizing", "vocalising"),
    ("vulgarize", "vulgarise"),
    ("vulgarized", "vulgarised"),
    ("vulgarizing", "vulgarising"),
    ("westminster", "westminster"),  # same (place name)
}


def build_us_uk_lookup() -> dict[str, str]:
    """Build a bidirectional lookup from the spelling pairs.

    Returns a dict where each key maps to its counterpart.
    Both US→UK and UK→US directions are included.
    """
    lookup: dict[str, str] = {}
    for us, uk in US_UK_SPELLING_PAIRS:
        if us != uk:  # skip identical entries
            lookup[us.lower()] = uk.lower()
            lookup[uk.lower()] = us.lower()
    return lookup


# Pre-build the lookup at module load time
_US_UK_LOOKUP: dict[str, str] | None = None


def is_us_uk_variant(word1: str, word2: str) -> bool:
    """Check if two words form a known US/UK spelling pair.

    Args:
        word1: First word (e.g., from PG text).
        word2: Second word (e.g., from scan text).

    Returns:
        True if the words are a known US/UK spelling variant pair.
    """
    global _US_UK_LOOKUP
    if _US_UK_LOOKUP is None:
        _US_UK_LOOKUP = build_us_uk_lookup()

    w1 = word1.lower().strip()
    w2 = word2.lower().strip()

    if w1 == w2:
        return False

    return _US_UK_LOOKUP.get(w1) == w2
