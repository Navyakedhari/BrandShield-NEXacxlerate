"""Small built-in vocabularies (no network, no data files).

Three layers:
* IMPERSONATION_WORDS - words scammers glue next to a brand ("support", "kyc"...).
* BENIGN_TRAILING_WORDS - other-business words ("bakery", "hotels"...). A *supplement*
  used to downgrade/label results; the main safety mechanism is that the full brand token
  must be present (or within one edit) before a candidate can be flagged at all.
* COMMON_WORDS - generic dictionary words so unknown glue can be segmented and labelled
  "dictionary" instead of "other".
"""

from __future__ import annotations

IMPERSONATION_WORDS: frozenset[str] = frozenset(
    """
    support official help helpline helpdesk care customer service services team india indian app apps login
    signin kyc rewards reward cashback offer offers giveaway free gift gifts claim refund refunds verify
    verification secure security safe update updates customercare contact toll tollfree complaint
    complaints grievance assist assistance agent agents executive manager head office hq online web portal
    wallet pay payment payments recharge loan loans card cards credit debit netbanking mobile banking mobilebanking
    account accounts bonus prize winner winners lucky lottery promo coupon voucher vouchers sale deal deals
    investment invest trading trade crypto bitcoin recovery recover unlock blocked suspended alert alerts
    id real original verified authorized authorised genuine
    """.split()
)

BENIGN_TRAILING_WORDS: frozenset[str] = frozenset(
    """
    bakery bakers hotel hotels resort resorts weather motors motor textiles textile foods food restaurant
    restaurants cafe cafes travels travel tours tour jewellers jewelers jewellery pharmacy school college
    university hospital clinic sports fitness gym salon spa studio studios furniture electronics hardware
    traders farm farms dairy sweets store stores mart bakehouse catering hostel gardens garden
    cinema theatre theater fashion boutique tailors garage autos auto cycles pets vet dental opticals
    """.split()
)

_COMMON = """
the and for you your our out all new top best get now one two three first last big small old pro plus max
mini lite go my me we us uk usa in on at to of by it is be or no yes up down home world global national
city town village north south east west central royal star stars sun moon sky blue red green black white
gold golden silver diamond fast quick easy smart super mega ultra prime elite active live life love
good great happy fun cool hot news media tv radio music video films film movie movies games game play
tech technology digital cloud data systems system labs lab works solutions group company co corp inc ltd
limited private public bank banks banking money finance capital fund funds insurance trust asset assets
shop shopping market mall buy sell store express delivery cab taxi ride rides fly air flights flight
health doctor care medical med meds fit food kitchen coffee tea juice water fire energy power solar
gas oil mobile phone phones net link links site page pages club fans fan community forum group chat talk
hub zone point place spot box bag book books read learn study class classes edu academy tutor
help desk mail email message msg sms call calls number numbers
year years day days night week month today tomorrow daily weekly monthly
mr mrs sir madam dear brother sister boss king queen prince princess lord master
ceo founder owner admin moderator staff crew family friends friend
hello hi welcome thanks thank please
bark band bang bunk tank rank sank bans banks lock link bake cake lake make take case base bass
work works worker jobs job career careers hire hiring news blog blogs fanpage fansite memes meme
""".split()

COMMON_WORDS: frozenset[str] = frozenset(w for w in _COMMON if len(w) >= 2 and "_" not in w)

# Words scored as "scam-flavoured" when they appear as the added part of a combo.
STRONG_SCAM_WORDS: frozenset[str] = frozenset(
    """
    support helpline helpdesk customercare care customer toll tollfree refund kyc cashback giveaway gift claim
    verify verification recovery unlock blocked suspended lottery winner prize rewards reward complaint
    official verified genuine authorised authorized grievance
    """.split()
)


def vocabulary() -> dict[str, int]:
    """word -> rank cost (lower is preferred by the segmenter)."""
    vocab: dict[str, int] = {}
    for w in COMMON_WORDS:
        vocab[w] = 2
    for w in BENIGN_TRAILING_WORDS:
        vocab.setdefault(w, 2)
    for w in IMPERSONATION_WORDS:
        vocab[w] = 1
    return vocab
