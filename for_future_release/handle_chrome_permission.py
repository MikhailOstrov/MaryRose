    # def _handle_chrome_permission_prompt(self):
    #     """
    #     Обрабатывает всплывающее окно разрешений Chrome: пытается разрешить доступ к микрофону.
    #     Безопасно выходим, если промпт отсутствует.
    #     """
    #     allow_site_ru = [
    #         "Разрешить при нахождении на сайте",
    #     ]
    #     allow_site_en = [
    #         "Allow on every visit",
    #         "Allow while on site",
    #         "Always allow on this site",
    #     ]
    #     allow_once_ru = [
    #         "Разрешить в этот раз",
    #     ]
    #     allow_once_en = [
    #         "Allow this time",
    #         "Allow once",
    #     ]

    #     def try_click_phrases(phrases, timeout_each=2):
    #         for phrase in phrases:
    #             xpaths = [
    #                 f"//button[normalize-space()='{phrase}']",
    #                 f"//button[contains(., '{phrase}')]",
    #                 f"//div[@role='button' and normalize-space()='{phrase}']",
    #                 f"//div[@role='button' and contains(., '{phrase}')]",
    #                 f"//span[normalize-space()='{phrase}']/ancestor::button",
    #             ]
    #             for xp in xpaths:
    #                 try:
    #                     btn = WebDriverWait(self.driver, timeout_each).until(
    #                         EC.element_to_be_clickable((By.XPATH, xp))
    #                     )
    #                     self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
    #                     btn.click()
    #                     logger.info(f"[{self.meeting_id}] Нажал кнопку разрешения: '{phrase}'")
    #                     return True
    #                 except Exception:
    #                     continue
    #         return False

    #     try:
    #         exists = self.driver.execute_script(
    #             "return !!document.querySelector('button, div[role\\'button\\']') && Array.from(document.querySelectorAll('button, div[role\\'button\\']')).some(el => (el.innerText||'').includes('Разрешить при нахождении') || (el.innerText||'').includes('Allow'));"
    #         )
    #         if not exists:
    #             logger.info(f"[{self.meeting_id}] Баннер разрешений не виден — пропускаю обработку.")
    #             return
    #     except Exception:
    #         pass

    #     if try_click_phrases(allow_site_ru, timeout_each=3) or try_click_phrases(allow_site_en, timeout_each=3):
    #         # time.sleep(0.1)
    #         self._save_screenshot("02b_permission_allowed_site")
    #         return
    #     if try_click_phrases(allow_once_ru, timeout_each=2) or try_click_phrases(allow_once_en, timeout_each=2):
    #         # time.sleep(0.1)
    #         self._save_screenshot("02b_permission_allowed_once")
    #         return
    #     logger.info(f"[{self.meeting_id}] Всплывающее окно разрешений не обнаружено.")