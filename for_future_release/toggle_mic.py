    # def toggle_mic_hotkey(self):
    #     """Простая эмуляция Ctrl+D для переключения микрофона в Meet.
    #     Без дополнительных проверок состояния и наличия кнопки.
    #     """
    #     try:
    #         # Стараемся сфокусировать страницу и убрать возможный фокус с инпутов
    #         try:
    #             self.driver.execute_script("window.focus();")
    #         except Exception:
    #             pass
    #         try:
    #             body = self.driver.find_element(By.TAG_NAME, 'body')
    #             body.click()
    #         except Exception:
    #             pass

    #         actions = ActionChains(self.driver)
    #         actions.key_down(Keys.CONTROL).send_keys('d').key_up(Keys.CONTROL).perform()
    #         logger.info(f"[{self.meeting_id}] Отправлено сочетание Ctrl+D (toggle mic)")
    #     except Exception as e:
    #         logger.warning(f"[{self.meeting_id}] Не удалось отправить Ctrl+D: {e}")