"""
身份生成工具：一个 Person 对象，随手造出注册要用的假姓名、邮箱、密码、手机号、地址等。

设计思想：
逆向注册要往表单里填一堆"看起来像真人"的资料。与其自己维护姓名库、地址库，不如
直接用成熟的 faker 库（社区维护，覆盖几十种语言的真实数据）。本文件在 faker 之上包
一层，只暴露注册最常用的那几样，方法名都用一个常见单词，调用时一看就懂。

两个实用特性：
1. 语言可选——lang="en" 造英文资料，lang="zh" 造中文资料，默认英文。
2. 可复现——传入 seed（种子）后，同一个种子每次造出完全一样的身份，方便调试时复现问题。

里面有什么：
- Person 类          一个假身份生成器
- Person.name()      全名
- Person.first()     名
- Person.last()      姓
- Person.user()      用户名
- Person.password()  密码（含大小写数字符号）
- Person.email()     邮箱地址
- Person.phone()     手机号
- Person.age()       年龄
- Person.birthday()  生日
- Person.city()      城市
- Person.company()   公司名
- Person.agent()     浏览器 User-Agent 字符串
- Person.all()       一次拿到上面所有字段组成的字典（最常用）

怎么调用：
    from tools.identity import Person

    who = Person(lang="en")               # 造一个英文假身份
    print(who.name(), who.email())        # 拿全名和邮箱

    who = Person(seed=42)                 # 固定种子，结果可复现
    data = who.all()                      # 一次拿全套资料填表
"""

from faker import Faker                                     # 成熟的假数据库，提供姓名/地址/公司等真实感数据
from datetime import date                                  # 根据同一生日计算一致年龄


# 语言标识到 faker 地区代码的对照：调用方只写简单的 en/zh，内部翻译成 faker 认的代码
# 放模块级是因为这份映射固定不变，所有 Person 实例共用一份
lang_map = {
    "en": "en_US",                                         # 英文 → 美国英语数据集
    "zh": "zh_CN",                                         # 中文 → 中国大陆中文数据集
}


class Person:
    """一个假身份生成器，一个实例对应一个可复现的随机来源。"""

    # --- 初始化：选定语言并准备 faker ---
    def __init__(self, lang="en", seed=None):
        # lang：造哪国资料，"en" 英文 / "zh" 中文
        # seed：随机种子，传了就能复现同样的结果，不传则每次都不同
        region = lang_map.get(lang, "en_US")               # 把简写语言翻译成 faker 地区代码，认不出就用英文兜底
        self.lang = lang                                   # 记住语言选择，个别方法（如手机号）要据此区分格式
        self.faker = Faker(region)                         # 按地区创建 faker 实例，之后所有假数据都从它来

        if seed is not None:                               # 只有明确传了种子才固定随机源
            self.faker.seed_instance(seed)                 # 绑定种子，让本实例的每次生成结果可复现

    # ==================== 姓名 ====================

    # --- 全名 ---
    def name(self):
        return self.faker.name()                           # 直接返回一个完整姓名，中英文格式由 faker 按地区处理

    # --- 名（first name） ---
    def first(self):
        return self.faker.first_name()                     # 返回名字部分，填"名"字段时用

    # --- 姓（last name） ---
    def last(self):
        return self.faker.last_name()                      # 返回姓氏部分，填"姓"字段时用

    # ==================== 账号 ====================

    # --- 用户名 ---
    def user(self):
        return self.faker.user_name()                      # 返回一个合法用户名（小写字母数字），可直接用于注册

    # --- 密码 ---
    def password(self, length=12):
        # length：密码长度，默认 12 位，够强又不至于太长
        # 四个 True 表示强制包含大写、小写、数字、特殊符号，满足大多数网站的密码强度要求
        return self.faker.password(length=length, special_chars=True, digits=True, upper_case=True, lower_case=True)

    # --- 邮箱 ---
    def email(self):
        return self.faker.email()                          # 返回一个邮箱地址；注意这是假域名，真收验证码请用 tools.mail

    # ==================== 联系方式 ====================

    # --- 手机号 ---
    def phone(self):
        return self.faker.phone_number()                   # 返回符合当前地区格式的电话号码

    # ==================== 年龄与生日 ====================

    # --- 年龄 ---
    def age(self, low=18, high=60):
        # low / high：年龄范围，默认成年到 60 岁，覆盖大多数注册场景
        return self.faker.random_int(min=low, max=high)    # 在范围内随机取一个整数年龄

    # --- 生日 ---
    def birthday(self, low=18, high=60, fmt="%Y-%m-%d"):
        # low / high：按年龄倒推生日的范围
        # fmt：日期输出格式，默认 年-月-日
        date = self.faker.date_of_birth(minimum_age=low, maximum_age=high)  # faker 按年龄算出一个真实生日
        return date.strftime(fmt)                          # 按指定格式转成字符串返回

    # ==================== 地址与机构 ====================

    # --- 城市 ---
    def city(self):
        return self.faker.city()                           # 返回一个城市名

    # --- 完整地址 ---
    def address(self):
        return self.faker.address().replace("\n", " ")     # 返回完整地址，把换行换成空格便于填进单行输入框

    # --- 公司名 ---
    def company(self):
        return self.faker.company()                        # 返回一个公司名称

    # ==================== 浏览器指纹 ====================

    # --- 浏览器 User-Agent ---
    def agent(self):
        return self.faker.user_agent()                     # 返回一个浏览器 UA 字符串，伪装请求来源时用

    # ==================== 一次拿全套 ====================

    # --- 生成一整套常用注册资料 ---
    def all(self):
        # 把注册最常用的字段一次性造齐，返回字典，调用方按需取用
        first = self.first()                               # 先生成一次名，后面全名复用
        last = self.last()                                 # 再生成一次姓，保证三个姓名字段能互相拼回去
        full = f"{last}{first}" if self.lang == "zh" else f"{first} {last}"  # 按地区习惯拼完整姓名
        born = self.faker.date_of_birth(minimum_age=18, maximum_age=60)  # 生日只生成一次
        today = date.today()                               # 以当前日期精确计算年龄
        age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))

        return {
            "name": full,                                  # 与 first/last 一致的完整姓名
            "first": first,                                # 名
            "last": last,                                  # 姓
            "user": self.user(),                           # 用户名
            "password": self.password(),                   # 密码
            "email": self.email(),                         # 邮箱（假域名，仅占位）
            "phone": self.phone(),                         # 手机号
            "age": age,                                   # 与 birthday 精确一致的年龄
            "birthday": born.strftime("%Y-%m-%d"),         # 同一生日格式化输出
            "city": self.city(),                           # 城市
            "company": self.company(),                     # 公司
            "agent": self.agent(),                         # 浏览器 UA
        }
