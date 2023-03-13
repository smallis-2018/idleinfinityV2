import argparse
import builtins
import re
import signal
import sys
import time
from random import uniform
from typing import Set, overload

import browser_cookie3
from loguru import logger
from requests import utils
from selenium import webdriver
from selenium.common import NoSuchElementException, TimeoutException, UnknownMethodException, WebDriverException, \
    ElementClickInterceptedException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support.relative_locator import locate_with
from selenium.webdriver.support.wait import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service as ChromeService

options = Options()
options.add_argument('-ignore -ssl-errors')
options.add_experimental_option("excludeSwitches", ['enable-automation', 'enable-logging'])
options.add_argument('--headless')

service = ChromeService(executable_path=ChromeDriverManager().install())
driver = None

try:
    driver = webdriver.Chrome(service=service, options=options)
except WebDriverException:
    logger.error("初始化Chrome失败")
    exit(1)

driver.implicitly_wait(2)
driver.set_window_size(1920, 1080)
# driver.maximize_window()


# 设置Log
logger.remove()
log_level = "INFO"
handler_id = logger.add(sys.stderr, level=log_level)

# 一些全局变量
# 已经探索过的区域
already_moved_region: Set[str] = set()

selected_role_id: int = 8916


def get_cookie_as_dict(domain_name: str) -> dict:
    try:
        cj = browser_cookie3.chrome(domain_name=domain_name)
        return utils.dict_from_cookiejar(cj)
    except RuntimeError:
        logger.error("获取登录信息失败")
        exit2(1)


def set_cookie():
    domain_name = "idleinfinity.cn"
    cookie_dict = get_cookie_as_dict(domain_name)
    for name, value in cookie_dict.items():
        logger.debug("name:{0}, value:{1}", name, value)
        driver.add_cookie(
            {'name': name,
             'value': value,
             'domain': domain_name}
        )


def show_map_handler():
    try:
        container = driver.find_element(By.XPATH, '//*[contains(@class,"dungeon-container")]')
        driver.execute_script("arguments[0].setAttribute(arguments[1],arguments[2])", container, 'class',
                              'panel-body dungeon-container')
        # driver.save_screenshot("./idleinfinity_screenshot.png")
    except NoSuchElementException:
        pass


def find_unready_region() -> set[str]:
    """
    找到所有迷雾边缘（可探索）区域，返回他们的id
    """
    all_unready_region: Set[str] = set()
    all_mask_region = driver.find_elements(By.XPATH, '//*[contains(@class,"mask")]')
    logger.info("待探索区域数：{0}", len(all_mask_region))
    for mask_region in all_mask_region:
        try:
            region = driver.find_element(locate_with(By.XPATH, '//*[contains(@class,"public")]').near(mask_region))
            region_id = region.get_attribute("id")
            logger.debug("区域Element id:{1},  session:{0}".format(region, region_id))
            all_unready_region.add(region_id)
        except NoSuchElementException:
            pass
    all_unready_region = all_unready_region.difference(already_moved_region)
    logger.info("剩余可探索区域数：{0}", len(all_unready_region))
    return all_unready_region


def find_region_by_id(id: str) -> WebElement:
    we = None
    try:
        we = driver.find_element(By.XPATH, '//*[@id="{0}"]'.format(id))
        logger.debug("current element session:{0}".format(we))
    except NoSuchElementException:
        logger.debug("没有id为{0}的区域", id)
    return we


def wait_kill():
    try:
        wait_time = WebDriverWait(driver, timeout=0.5).until(
            lambda d: d.find_element(By.XPATH, '//*[@id="time"]')).text
        logger.info("等待杀死怪物，预计等待时间{0}秒", int(wait_time))
        time.sleep(int(wait_time) + 1)
    except TimeoutException:
        logger.info("正在杀怪")
        raise TimeoutException


def back_to_map() -> bool:
    driver.get("https://www.idleinfinity.cn/Map/Dungeon?id={0}".format(selected_role_id))
    show_map_handler()
    logger.info("进入地图")
    try:
        driver.find_element(By.XPATH, '//*[text()="非法操作：此地图没有秘境"]')
        logger.error("所在层数没有秘境，打NM")
        return False
    except NoSuchElementException:
        # time.sleep(2)
        return True


def move(regions: set[str]):
    for region in regions:
        # 方法一：点击区域进入
        # 方法二：请求该区域，但会引起服务端报错。可能会被检测。
        try:
            find_region_by_id(region).click()
        except ElementClickInterceptedException:
            pass
        logger.info("移动到未知区域，id:{0}", region)
        already_moved_region.add(region)

        try:
            if re.search(r'InDungeon', driver.current_url, re.I) is not None:
                wait_kill()
                back_to_map()
            time.sleep(uniform(0.5, 1))
        except TimeoutException:
            back_to_map()
            pass


def reset() -> bool:
    back_to_map()
    try:
        reset_button = driver.find_element(By.XPATH, '//*[normalize-space(text())="重置"]')
        reset_button.click()
        WebDriverWait(driver, timeout=2).until(
            lambda d: d.find_element(By.XPATH, '//*[normalize-space(text())="确认"]'))
        driver.find_element(By.XPATH, '//button[@class="btn btn-primary btn-xs confirm-ok"]').click()
        logger.info("重置地图")
        return True
    except TimeoutException or NoSuchElementException:
        logger.error("重置地图失败，已结束脚本")
        return False


def check_monster() -> Set[str]:
    ids: Set[str] = set()
    monsters = driver.find_elements(By.XPATH, '//a[contains(@class,"monster")]')
    for m in monsters:
        ids.add(m.get_attribute("id"))
    return ids


def get_role_list() -> dict:
    driver.get('https://www.idleinfinity.cn/Home/Index')
    roles = {}
    try:
        role_name_eles = driver.find_elements(By.XPATH, '//*[text()="选择"]/parent::div[1]//preceding-sibling::span[3]')
        role_checker_eles = driver.find_elements(By.XPATH, '//*[text()="选择"]')

        for role_name_ele, role_checker_ele in zip(role_name_eles, role_checker_eles):
            role_name = role_name_ele.text
            role_id = re.findall(r'\d*$', role_checker_ele.get_attribute("href"))[0]
            roles[role_id] = role_name
            logger.debug("角色名字：{0}，角色id：{1}", role_name, role_id)
    except NoSuchElementException:
        logger.error("获取角色信息错误，请检查是否创建角色")
    return roles


def check_login() -> bool:
    driver.get('https://www.idleinfinity.cn/Home/Index')
    # 判断登录是不是过期
    url = driver.current_url
    r = re.search(r'Login', url, re.I)
    logger.debug("当前页面：{0}, r:{1}", url, r)
    if r is not None:
        logger.error("登录过期，请手动打开Chrome浏览器登录一次后，关闭浏览器即可")
        driver.delete_all_cookies()
        return False
    return True


def get_san() -> str:
    return driver.find_element(By.XPATH, '//*[normalize-space(text())="SAN："]/child::span[1]').text


def exit2(code):
    try:
        sys.exit(code)
    except SystemExit:
        pass
    finally:
        driver.quit()
        service.stop()


signal.signal(signal.SIGINT, exit2)
signal.signal(signal.SIGTERM, exit2)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--log_level', type=str, default="INFO", help="Out log level")
    args = parser.parse_args()

    log_level = args.log_level

    driver.get('https://www.idleinfinity.cn/Home/Index')
    set_cookie()

    if not check_login():
        exit2(1)

    # 获取角色列表
    roles = get_role_list()
    for role_id, role_name in roles.items():
        print("角色名字：{0}，角色id：{1}".format(role_name, role_id))
    pick_id = input("请输入角色id：")
    try:
        pick_id = int(pick_id)
    except ValueError:
        logger.error("输入错误，请重试")
        exit2(1)

    if roles[str(pick_id)] is None:
        logger.error("没找到角色，爬")
    else:
        selected_role_id = pick_id

    if not back_to_map():
        exit2(1)

    while True:
        r = find_unready_region()
        san = get_san()
        logger.info('当前SAN值：{0}', san)

        if int(san) <= 0:
            exit2(0)

        if len(r) <= 0:
            logger.info("当前地图无可搜索区域")
            monster_id_set = check_monster()
            if len(monster_id_set) > 0:
                logger.info("开始清空怪物")
                # 清空一下已经探索过的区域
                already_moved_region.clear()
                move(monster_id_set)
            else:
                if not reset():
                    exit2(1)
        else:
            move(r)
