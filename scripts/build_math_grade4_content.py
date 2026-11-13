#!/usr/bin/env python3
"""Build original Shanghai 2026 grade-four mathematics practice catalogs."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / 'subject_content/math'
STATIC = ROOT / 'static/subject-content/math'
EDITION = '2026秋沪教版数学四年级上册（五·四学制，2024年审核通过的新版）'
NOTE = '前30题为基础练习90分，最后2题为思维拓展10分，合计100分，可分层完成。按各答题栏的最终答案自动计分，分栏小题可分别得分；解题过程供订正参考，不计入自动评分。展开思路提示不扣分。'
REFERENCES = [
    {'label': '上海教育出版社五四学制数学四上新版封面', 'url': 'https://3wm.seph.com.cn/Uploads/2026-09-01/854353a7e9304748be5cd9e44bb627cb.jpg'},
    {'label': '2026秋沪教五四四上数学公开目录', 'url': 'http://www.dzkbw.com/books/hjb/shuxue/wsz4s_2026/'},
    {'label': '新版章节目录交叉核对', 'url': 'https://guchenlong.github.io/'},
]
UNITS = [
    ('运算关系与运算律', [
        '加法各部分的关系：一个加数=和−另一个加数；减法中，被减数=差+减数，减数=被减数−差。',
        '乘法各部分的关系：一个因数=积÷另一个非零因数；除法中，被除数=商×除数（有余数时还要加上余数）。',
        '加法交换律与结合律可改变运算顺序、组合方式：a+b=b+a，(a+b)+c=a+(b+c)。',
        '乘法交换律与结合律：a×b=b×a，(a×b)×c=a×(b×c)；常见凑整搭配有25×4=100、125×8=1000。',
        '乘法分配律：(a+b)×c=a×c+b×c。自编例：36×99+36=36×(99+1)=3600。',
        '同级运算按从左往右计算。减法、除法不能随意交换或结合：72÷8×3=27，不等于72÷(8×3)。',
        '检验未知数时把求出的数代回原式。自编例：□+276=1000，□=724，检验724+276=1000。',
    ]),
    ('四面八方的学问', [
        '四个主要方向是东、南、西、北，四个中间方向是东北、东南、西南、西北。',
        '先确认图上的方向标识；本套示意图均明确标注“上北下南，左西右东”，不能把屏幕上的左右直接当作人的左右。',
        '描述位置先确定观察点。同一位置关系换一个观察点，方向相反：甲在乙东北，乙就在甲西南。',
        '相对的方向成对记忆：东与西、南与北、东北与西南、西北与东南。',
        '判断转向先想清原来的朝向。自编例：面向东时左转，转后面向北；面向南时左手指向东。',
        '读路线图时逐段核对出发点、行进方向和目的地；不要把终点相对起点的方向当作每一段的行进方向。',
    ]),
    ('整数的四则运算与应用题', [
        '没有括号时先乘除、后加减；同级运算从左往右。有括号先算括号里面，再按顺序计算。',
        '自编例：4800−(1680+920)=4800−2600=2200。括号改变了运算顺序，不能漏算。',
        '单价×数量=总价；总价÷数量=单价；总价÷单价=数量。每个数都要带着它的含义思考。',
        '工作效率×工作时间=工作量。多台相同机器同时工作时，先明确给出的效率是每台效率还是总效率。',
        '分析应用题按“求什么→需要什么条件→先算哪一步”进行；可画线段图、列表或分步列式辅助理解。',
        '有余数除法要满足余数小于除数；检验用“商×除数+余数=被除数”。',
        '分装问题要结合实际：求装满几盒可取整数商；求全部装完最少几盒，有剩余时还要增加一盒。',
        '估算用于检查数量级是否合理，不能代替题目明确要求的准确计算。注意结果的单位。',
    ]),
    ('大数的认识与改写', [
        '十进制相邻计数单位间的进率为10；分级读数时，从右向左每四位分为一级，依次是个级、万级、亿级。',
        '数位表示数字所在的位置，计数单位表示这个位置上的一个单位，数字的数值等于数字乘相应计数单位。',
        '自编例：7305040中的3在十万位，表示3个十万，即300000。中间的0也要占据相应数位。',
        '按组成写数先确定高位，再逐位填数，缺少的数位补0。自编例：36个万和508个一组成360508。',
        '整万数改写为“万”作单位，去掉末尾4个0，再写单位“万”；整亿数改写同理去掉末尾8个0。改写不改变原数大小。',
        '四舍五入到万位，观察千位；到亿位，观察千万位。被观察的数字小于5舍去，大于或等于5向保留位进1。',
        '自编例：495600≈50万；39995000≈4000万。连续进位时，保留位之前也可能发生变化。',
        '区分“=”与“≈”：整万数改写用等号，省略尾数得到近似数用约等号。题目要求以万为单位时要写清对应单位。',
    ]),
    ('度量衡的故事', [
        '本单元主题关注度量单位及统一计量的意义：度与长短有关，量与容量有关，衡与轻重有关。',
        '同一种量需要使用统一单位，才能公平比较和交流；不同年代的同名传统单位不一定相同，不随意套用现代换算。',
        '配套基础复习：1千米=1000米，1吨=1000千克，1千克=1000克，1升=1000毫升。完整单位范围以本版课本和教师要求为准。',
        '大单位换成小单位乘进率，小单位换成大单位除以进率；先辨别是长度、质量还是容量，不能跨不同种类直接比较。',
        '自编例：4吨35千克=4035千克；3050毫升=3升50毫升。复名数量先分别换算，再合并或比较。',
        '选择单位要结合实际：一杯果汁的容量常用毫升，一辆货车的载质量可用吨，城市间的距离可用千米。',
    ]),
    ('分数的初步认识', [
        '分数建立在平均分的基础上。分母表示平均分成几份，分子表示取了这样的几份；没有平均分时，不能直接写成对应的几分之几。',
        '同分母分数比较大小，分子大的分数较大；同分子分数比较时，要保证表示的是同一个整体。',
        '同分母分数相加减，分母不变，分子相加减。自编例：3/8+2/8=5/8，不是5/16。',
        '表示一个完整的整体时，可写成分子与分母相同的分数，例如1=8/8；读题时先明确单位“1”是什么。',
        '分数与长度结合时注意单位。自编例：一条彩带长9/10米，用去3/10米和2/10米，还剩4/10米。',
        '本套答题栏用a/b输入分数，例如5/8；不要求必须约分，等值分数按相同大小判断。',
    ]),
    ('线与角', [
        '线段有两个端点，能量出长度；射线有一个端点，可向一端无限延伸；直线没有端点，可向两端无限延伸。',
        '角由一个顶点和从这个顶点引出的两条射线组成；角的大小与两边张开的程度有关，与画出的边长无关。',
        '常见角的大小：直角90°、平角180°、周角360°。锐角小于90°，钝角大于90°且小于180°。',
        '用量角器时，中心对准角的顶点，零刻度线对准角的一条边，再看另一条边经过的刻度；注意内外两圈读数。',
        '角的计算先确定整体角与部分角。自编例：一个平角被分成68°和另一个角，另一个角为180°−68°=112°。',
        '看图不能只凭“看起来像直角”判断；图上已知角度、直线条件和明确标记才是计算依据。',
    ]),
    ('数学广场', [
        '将已学的数量关系、运算和图形知识联系起来，先整理条件，再选择列表、尝试、逆推或检验等方法解决新问题。',
        '遇到条件较多的题，先整理总数、总量等限制条件，再有顺序地尝试，避免重复或遗漏。',
        '自编方法例：10枚1元和5元硬币共34元，先把10枚都看作1元，共10元；相差24元，每换一枚5元多4元，所以5元有6枚。',
        '找到答案后同时检查所有条件，不能只满足其中一个条件；可用另一种方法或实际含义再次验证。',
    ]),
    ('复习', [
        '先复习运算关系与运算顺序，再复习方向、大数、计量、分数和角；把易错原因写成一句提醒。',
        '填空只填题目所问的数或方向；多栏题逐栏作答，特别检查商与余数、不同单位的两个数。',
        '应用题先在纸上列式并写答句，再填写在线答题栏；在线成绩只反映已公布的答题栏计分，不等同于教师对解题过程的评分。',
        '本套练习是自编的复习材料，期中划分为第1–4单元；学校教学进度不同，以教师划定范围为准。',
    ]),
]


def field(value, *, label='答案', points=2, unit='', fmt='integer', identifier='answer', accepted=None):
    answer = str(value)
    return {'id': identifier, 'label': label, 'points': points, 'unit': unit, 'format': fmt,
            'answer': answer, 'accepted_answers': accepted or [answer],
            'unit_aliases': {'度': ['°'], '毫升': ['mL', 'ml'], '升': ['L', 'l'], '千米': ['km'], '米': ['m'], '千克': ['kg'], '吨': ['t']}.get(unit, []),
            'hint': '用a/b输入分数，可不约分。' if fmt == 'fraction' else '按上面的单位填写数字。' if fmt != 'text' else '填写方向或题目要求的文字。'}


def choice(prompt, options, correct, explanation, unit, diagram=None):
    result = {'type': 'choice', 'prompt': prompt, 'options': [{'id': chr(97+i), 'text': text} for i,text in enumerate(options)],
              'answer': chr(97+correct), 'points': 2, 'explanation': explanation, 'unit': unit}
    if diagram: result.update(diagram_url=f'/static/subject-content/math/{diagram[0]}.svg', diagram_alt=diagram[1])
    return result


def written(kind, prompt, fields, explanation, unit, diagram=None):
    result = {'type': kind, 'prompt': prompt, 'fields': fields, 'points': sum(item['points'] for item in fields), 'explanation': explanation, 'unit': unit}
    if diagram: result.update(diagram_url=f'/static/subject-content/math/{diagram[0]}.svg', diagram_alt=diagram[1])
    return result


MID_MAP = ('math-g4-midterm-map', '示意图上北下南，学校在中央，图书馆在学校东北，公园在学校正南，书店在学校正东。')
FINAL_MAP = ('math-g4-final-map', '示意图上北下南，校门在操场的西北。')
ANGLE = ('math-g4-straight-angle', 'A、O、B在同一直线上，射线OC在直线上方，角COB为68度，角AOC待求。')


def midterm():
    q = [
        choice('(25+37)+63=25+(37+63)，这里运用的是哪条运算律？', ['加法交换律','加法结合律','乘法结合律','乘法交换律'], 1, '只改变三个加数的组合方式，没有交换加数位置，运用加法结合律。',1),
        choice('下面哪个算式与48×25相等？', ['48×(20+5)','48×20+5','40×25+8','48+25'],0,'25=20+5，因此48×25=48×(20+5)。展开时20和5都要分别乘48。',1),
        choice('如图，从图书馆看，学校在图书馆的哪个方向？', ['东北','西北','东南','西南'],3,'图书馆在学校东北，换到图书馆观察，学校就在相反的西南方向。',2,MID_MAP),
        choice('小明原来面向东，向左转后面向北。这时他的后方是哪个方向？',['东','西','南','北'],2,'转后面向北，后方与北相反，是南。',2),
        choice('420÷35×2的正确结果是多少？',['6','12','24','420'],2,'除法和乘法是同级运算，从左往右：420÷35=12，12×2=24。',3),
        choice('总价不变，商品单价由8元降为4元，能买到的数量怎样变化？',['保持不变','变为原来的一半','比原来多4件','变为原来的2倍'],3,'单价变为原来的一半，总价不变，数量变为原来的2倍。',3),
        choice('7305040中的数字3在哪个数位上？',['十万位','万位','百万位','千位'],0,'从右往左数，第六位是十万位。3表示3个十万，即300000。',4),
        choice('495600四舍五入到万位，约是多少万？',['49万','50万','496万','500万'],1,'495600的千位是5，万位上的9要进1，并向十万位继续进位，得到50万。',4),
        written('fill','□+276=1000，□里应填多少？',[field(1000-276)],'1000−276=724，代回检验：724+276=1000。',1),
        written('fill','□÷16=25，□里应填多少？',[field(25*16)],'被除数=商×除数，25×16=400。',1),
        written('fill','125×8×37=________。',[field(125*8*37)],'先算125×8=1000，再算1000×37=37000。',1),
        written('fill','如图，站在学校面向公园时，左手指向哪个方向？',[field('东',fmt='text',label='左手方向',accepted=['东','东方','东面','正东'])],'面向公园就是面向南；面向南时左手指向东。',2,MID_MAP),
        written('fill','一个数由36个万和508个一组成，这个数是多少？',[field(36*10000+508)],'36个万是360000，再加508，得到360508。',4),
        written('fill','80004006是几位数？它最高位的计数单位是什么？',[field(8,label='位数',points=1,identifier='digits'),field('千万',label='最高位的计数单位',points=1,fmt='text',identifier='counting_unit')],'从最高位数到个位，共8个数位；最高位是千万位，对应的计数单位是千万。“千万位”是数位名称，不是计数单位。',4),
        written('fill','46000000=________万。',[field(46000000//10000,unit='万')],'整万数改写：46000000÷10000=4600，所以等于4600万。',4),
        written('fill','700000000=________亿。',[field(7,unit='亿')],'700000000含有7个亿，改写成7亿，数的大小不变。',4),
        written('fill','39995000四舍五入到万位，约是________万。',[field(4000,unit='万')],'千位为5，向万位进1，连续进位得到40000000，即4000万。',4),
        written('fill','一台机器每分钟加工18个零件，5分钟加工多少个？',[field(18*5,unit='个',label='零件数')],'工作量=工作效率×时间，18×5=90（个）。',3),
        written('fill','540本图书，每箱装18本，需要多少箱？',[field(540//18,unit='箱')],'总本数÷每箱本数=箱数，540÷18=30（箱）。',3),
        written('fill','81×(40+2)=81×40+81×□，□里应填多少？',[field(2)],'运用乘法分配律，括号里的40和2都分别乘81。',1),
        written('calculation','计算：4800−(1680+920)。',[field(4800-(1680+920),points=5)],'先算括号：1680+920=2600；再算4800−2600=2200。',3),
        written('calculation','用合适的方法计算：625×16。',[field(625*16,points=5)],'可把16分成8×2：625×8×2=5000×2=10000。',1),
        written('calculation','计算：3600÷(25×8)。',[field(3600//(25*8),points=5)],'先算25×8=200，再算3600÷200=18。',3),
        written('calculation','计算：(720−480)×35。',[field((720-480)*35,points=5)],'720−480=240，240×35=8400。',3),
        written('calculation','计算987÷24，分别填写商和余数。',[field(987//24,label='商',points=3,identifier='quotient'),field(987%24,label='余数',points=2,identifier='remainder')],'24×41=984，987−984=3，商41余3；3小于24。',3),
        written('calculation','计算：704÷32。',[field(704//32,points=5)],'32×22=704，因此704÷32=22。可用乘法检验。',3),
        written('calculation','用简便方法计算：36×99+36。',[field(36*99+36,points=5)],'把36看作36×1，36×99+36×1=36×(99+1)=3600。',1),
        written('calculation','计算：684÷(57−19)。',[field(684//(57-19),points=5)],'先算括号57−19=38，再算684÷38=18。',3),
        written('application','每本练习本8元，购买24本，付给营业员200元。总价是多少元？应找回多少元？',[field(8*24,label='总价',points=2,unit='元',identifier='total'),field(200-8*24,label='找回的钱',points=3,unit='元',identifier='change')],'先求总价：8×24=192（元）；再求找零：200−192=8（元）。',3),
        written('application','3台相同的机器同时工作，每台每小时加工18个零件。4小时一共加工多少个零件？',[field(3*18*4,label='加工总数',points=5,unit='个')],'每小时总效率为18×3=54（个）；4小时共54×4=216（个）。',3),
    ]
    return q


def final():
    return [
        choice('计算125×32时，把32写成8×4，再先算125×8，主要运用了哪条运算律？',['乘法交换律','加法结合律','乘法分配律','乘法结合律'],3,'把125×(8×4)改为(125×8)×4，改变的是因数的组合方式，运用乘法结合律。',1),
        choice('如图，从校门看操场，操场在校门的哪个方向？',['东南','东北','西南','西北'],0,'校门在操场西北，从校门观察操场，方向相反，是东南。',2,FINAL_MAP),
        choice('30506000四舍五入到万位，正确结果是哪个？',['3050万','305060万','3051万','3060万'],2,'千位为6，向万位进1，30506000≈30510000=3051万。',4),
        choice('表示一杯橙汁的容量，下面哪个单位最合适？',['吨','毫升','千米','克'],1,'容量要用容量单位，一杯橙汁通常用毫升表示。吨、克是质量单位，千米是长度单位。',5),
        choice('3/8+2/8的结果是多少？',['5/16','5/8','1','1/8'],1,'同分母分数相加，分母8不变，分子3+2=5，结果是5/8。',6),
        choice('一个角是75°，再增加多少度就成为平角？',['15°','75°','105°','285°'],2,'平角为180°，180°−75°=105°。',7),
        choice('只有一个端点，并能向一端无限延伸的是哪个？',['射线','线段','直线','角'],0,'射线有一个端点，能向一端无限延伸；线段有两个端点，直线没有端点。',7),
        choice('把498看作500、21看作20来估算498×21，估算结果是多少？',['1000','5000','2000','10000'],3,'500×20=10000。估算得到大约10000，不能把估算值当作准确积。',9),
        written('fill','□−385=615，□里应填多少？',[field(615+385)],'被减数=差+减数，615+385=1000。',1),
        written('fill','864÷(9×8)=________。',[field(864//72)],'先算9×8=72，再算864÷72=12。',3),
        written('fill','65070803中的数字7在哪个数位上？',[field('万',fmt='text',label='数位',accepted=['万','万位'])],'从右向左分级为6507万0803，7在万位，表示7个万。',4),
        written('fill','409050000=________万。',[field(409050000//10000,unit='万')],'409050000÷10000=40905，所以等于40905万。',4),
        written('fill','80996000四舍五入到万位，约是________万。',[field(8100,unit='万')],'千位是6，要向万位进1，并连续进位，得到81000000，即8100万。',4),
        written('fill','4吨35千克=________千克。',[field(4*1000+35,unit='千克')],'4吨=4000千克，再加35千克，得到4035千克。',5),
        written('fill','3050毫升=________升________毫升。',[field(3,label='升数',points=1,unit='升',identifier='litres'),field(50,label='余下的毫升数',points=1,unit='毫升',identifier='millilitres')],'1升=1000毫升，3050毫升=3000毫升+50毫升=3升50毫升。',5),
        written('fill','7千米80米=________米。',[field(7080,unit='米')],'7千米=7000米，再加80米，得到7080米。',5),
        written('fill','8个1/10合起来是多少？',[field('8/10',fmt='fraction')],'分数单位是1/10，8个这样的单位合起来是8/10；等值分数也正确。',6),
        written('fill','7/9−2/9=________。',[field('5/9',fmt='fraction')],'同分母分数相减，分母9不变，分子7−2=5，结果为5/9。',6),
        written('fill','一周角是多少度？',[field(360,unit='度')],'围绕顶点完整转一周形成周角，为360°。',7),
        written('fill','如图，A、O、B在同一直线上，∠COB=68°。∠AOC是多少度？',[field(180-68,unit='度')],'∠AOC与∠COB合起来是平角180°，所以∠AOC=180°−68°=112°。',7,ANGLE),
        written('calculation','用合适的方法计算：408×25。',[field(408*25,points=5)],'(400+8)×25=400×25+8×25=10000+200=10200。',1),
        written('calculation','计算：7200÷(24×5)。',[field(7200//(24*5),points=5)],'24×5=120，7200÷120=60。',3),
        written('calculation','计算：5632÷64。',[field(5632//64,points=5)],'64×88=5632，所以5632÷64=88。',3),
        written('calculation','计算：1025−(375+248)。',[field(1025-(375+248),points=5)],'先算括号375+248=623，再算1025−623=402。',3),
        written('calculation','计算900÷28，分别填写商和余数。',[field(900//28,label='商',points=3,identifier='quotient'),field(900%28,label='余数',points=2,identifier='remainder')],'28×32=896，900−896=4；商32余4，余数4小于28。',3),
        written('calculation','用简便方法计算：37×102−37×2。',[field(37*102-37*2,points=5)],'提取共同因数37：37×(102−2)=37×100=3700。',1),
        written('calculation','计算：5/12+4/12−2/12。',[field('7/12',points=5,fmt='fraction')],'同分母分数加减，分母12不变，分子5+4−2=7，得到7/12。',6),
        written('calculation','计算：180°−(35°+48°)。',[field(180-35-48,points=5,unit='度')],'先算35°+48°=83°，再算180°−83°=97°。',7),
        written('application','48人平均分成4组开展活动。每组购买3袋材料，每袋25元。每组有多少人？四组材料总共需要多少元？',[field(48//4,label='每组人数',points=2,unit='人',identifier='people'),field(4*3*25,label='材料总价',points=3,unit='元',identifier='cost')],'每组48÷4=12（人）。总材料费按4组计算：25×3×4=300（元），不再乘48。',3),
        written('application','有5升果汁，每瓶装250毫升，恰好装完。能装满多少瓶？把这些瓶子平均装入4盒，每盒多少瓶？',[field(5000//250,label='总瓶数',points=2,unit='瓶',identifier='bottles'),field(5000//250//4,label='每盒瓶数',points=3,unit='瓶',identifier='per_box')],'5升=5000毫升；5000÷250=20（瓶）；20÷4=5（瓶）。',5),
    ]


def challenge_questions(kind):
    hints_division = ['先看被除数相差多少，以及商相差多少。两个余数相同，差量来自几个完整的除数。',
                      '用被除数的差除以商的差，求出除数，再代回原来的被除数求余数；最后检验余数小于除数。']
    hints_transfer = ['从最后一步开始，按相反的运算顺序倒着推回原来的人数。',
                      '先求另一组人数。每有1人从一组转到另一组，两组的人数差会减少2人。',
                      '找出两组人数相等的情况，再判断是否满足“少于”；相等还不符合要求。']
    if kind == 'midterm':
        first = written('application','思维拓展①：一个有余数的除法，误把被除数527写成587，得到的商比正确的商大5，余数恰好相同。原来的除数和余数分别是多少？',
                        [field(12,label='除数',points=3,identifier='divisor'),field(11,label='余数',points=2,identifier='remainder')],
                        '两个被除数相差587−527=60，商多5而余数相同，说明多出了5个除数；除数为60÷5=12。527÷12=43……11，所以原余数是11。检验：587=12×48+11，且11<12。',3)
        second = written('application','思维拓展②：420位同学全部只在A、B两组中选择一组。A组人数先除以2，再乘3，最后加36，得到420。A组原来有多少人？现在从A组改选B组，要使A组人数少于B组，至少需要多少人改选？',
                         [field(256,label='A组原来人数',points=2,unit='人',identifier='original_a'),field(47,label='至少改选人数',points=3,unit='人',identifier='transfer')],
                         '逆推：(420−36)÷3×2=256（人），B组为420−256=164（人）。原来相差92人，每改选1人，两组差减少2。改选46人后两组都是210人，还没有“少于”；改选47人后A组209人、B组211人，因此至少47人。',3)
    else:
        first = written('application','思维拓展①：小林计算有余数的除法时，把被除数873误写成789，得到的商比正确的商小6，余数相同。正确的除数和余数分别是多少？',
                        [field(14,label='除数',points=3,identifier='divisor'),field(5,label='余数',points=2,identifier='remainder')],
                        '被除数减少873−789=84，商减少6而余数相同，说明减少了6个除数；除数是84÷6=14。873÷14=62……5，所以余数是5。检验：789=14×56+5，且5<14。',3)
        second = written('application','思维拓展②：科技社和阅读社共有504人，每人只参加一个社团。科技社人数先除以3，再加24，最后乘2，得到272。科技社原来有多少人？若只从科技社转入阅读社，要使科技社人数少于阅读社，至少转入多少人？',
                         [field(336,label='科技社原来人数',points=2,unit='人',identifier='original_a'),field(85,label='至少转入人数',points=3,unit='人',identifier='transfer')],
                         '先逆推：(272÷2−24)×3=336（人），阅读社为504−336=168（人）。原来相差168人，转入84人后两社都是252人，还不符合“少于”。转入85人后科技社251人、阅读社253人，所以至少转入85人。',8)
    first.update(difficulty='challenge',hints=hints_division)
    second.update(difficulty='challenge',hints=hints_transfer)
    return [first,second]


def build():
    (CONTENT/'papers').mkdir(parents=True,exist_ok=True)
    STATIC.mkdir(parents=True,exist_ok=True)
    papers, knowledge = [], []
    for kind, scope, start, end, questions in [('midterm','第1–4单元',1,4,midterm()),('final','全册第1–9单元',1,9,final())]:
        questions.extend(challenge_questions(kind))
        paper_id=f'math-shanghai-grade4-term1-{kind}-2026'
        title=f'上海四年级上册数学 · 2026新版{ "期中练习（第1–4单元）" if kind=="midterm" else "期末综合练习（全册）"}'
        for i,question in enumerate(questions,1): question['id']=f'q{i:02d}'
        assert len(questions)==32 and sum(question['points'] for question in questions)==100
        data={'schema_version':1,'subject':'math','id':paper_id,'title':title,'grade':4,'term':1,'scope':scope,'edition':EDITION,
              'time_minutes':45 if kind=='midterm' else 60,'grading_note':NOTE,'references':REFERENCES,'questions':questions}
        (CONTENT/'papers'/f'{paper_id}.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
        knowledge_id=f'math-grade4-term1-{kind}-points'
        papers.append({'id':paper_id,'title':title,'grade':4,'term':1,'exam_kind':kind,'scope':scope,'edition':EDITION,
                       'description':'32道原创题：8道选择、12道填空、8道计算、4道应用，其中最后2题为思维拓展。基础90分、拓展10分；分栏小题可分别得分。',
                       'question_count':32,'time_minutes':data['time_minutes'],'interactive':True,'knowledge_id':knowledge_id,
                       'pdf_url':f'/static/subject-content/math/math-grade4-term1-{kind}-2026.pdf'})
        knowledge.append({'id':knowledge_id,'title':f'上海四年级上册数学 · {"期中" if kind=="midterm" else "期末"}知识点梳理（2026新版）',
                          'grade':4,'term':1,'scope':scope,'edition':EDITION,
                          'description':'按2026秋新版公开目录整理核心方法和易错点，例题均为原创；完整课本内容与学校进度以教师要求为准。',
                          'pdf_url':f'/static/subject-content/math/math-grade4-term1-{kind}-knowledge-2026.pdf',
                          'sections':[{'title':f'第{i}单元 · {UNITS[i-1][0]}','items':UNITS[i-1][1]} for i in range(start,end+1)] + [
                              {'title':'思维拓展 · 差量、逆推与临界条件','items':[
                                  '拓展练习用来挑战思考。可以先完成前30题的基础部分，再尝试最后两题；基础90分、拓展10分，合计100分。',
                                  '商与余数：被除数=除数×商+余数。同一除数、相同余数时，被除数的差=除数×商的差。先求除数，再代回原式求余数。',
                                  '逆推：从最后得到的结果出发，按相反顺序做相反运算。例如先除再乘、最后加数，倒着推时先减去最后加数。',
                                  '人数转移：1人从A组转到B组时，A少1、B多1，两组人数之差减少2；不是只减少1。',
                                  '临界判断：“少于”不包括相等。找到人数相等或刚好跨过相等的位置，再检验转移前后人数和总数是否都满足条件。',
                                  '遇到较难题，可先完成基础部分，再依次查看思路提示。订正时说清为什么少转1人不行，比只记住答案更重要。',
                              ]}]})
    (CONTENT/'catalog.json').write_text(json.dumps({'schema_version':1,'subject':'math','papers':papers,'knowledge':knowledge},ensure_ascii=False,indent=2)+'\n')
    svg_start='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 420 300"><rect width="420" height="300" rx="18" fill="#f4f8ff"/><g font-family="sans-serif" fill="#24436d" font-size="16">'
    compass='<path d="M376 72V26l-8 12m8-12 8 12" fill="none" stroke="#3768a9" stroke-width="3"/><text x="367" y="20">北</text><text x="20" y="286" font-size="13">上北下南，左西右东（位置示意）</text>'
    def label(x,y,name):
        return f'<circle cx="{x}" cy="{y}" r="7" fill="#4b82d2"/><text x="{x+12}" y="{y+5}">{name}</text>'
    (STATIC/'math-g4-midterm-map.svg').write_text(svg_start+compass+label(160,150,'学校')+label(290,65,'图书馆')+label(160,240,'公园')+label(290,150,'书店')+'</g></svg>')
    (STATIC/'math-g4-final-map.svg').write_text(svg_start+compass+label(95,80,'校门')+label(225,190,'操场')+'</g></svg>')
    (STATIC/'math-g4-straight-angle.svg').write_text('''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 420 240"><rect width="420" height="240" rx="18" fill="#f4f8ff"/><g stroke="#3768a9" stroke-width="3" fill="none"><path d="M30 180H390M210 180L270 32"/><path d="M265 180A55 55 0 0 0 230.6 129" stroke-width="2"/><path d="M145 180A65 65 0 0 1 234.3 119.7" stroke-width="2" stroke="#d87a42"/></g><g font-family="sans-serif" font-size="18" fill="#24436d"><text x="25" y="207">A</text><text x="202" y="207">O</text><text x="384" y="207">B</text><text x="275" y="30">C</text><text x="265" y="137">68°</text><text x="144" y="122">?°</text><text x="30" y="230" font-size="13">A、O、B在同一直线上；图形不按角度精确绘制。</text></g></svg>''')
    print('Built two 32-question papers, matching knowledge and three original diagrams.')


if __name__=='__main__': build()
